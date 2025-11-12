from __future__ import annotations
from pathlib import Path
from typing import Final

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .collectors import collect_all
from .renderer import render_status as render_status_pillow
from .utils import ensure_dir
from .bg_provider import resolve_background
import os
import astrbot.api.message_components as Comp


PLUGIN_NAME: Final[str] = "astrbot_plugin_picstatus"
ALIASES: Final[set[str]] = {"状态", "zt", "yxzt", "status", "运行状态"}
CACHE_DIR = Path(__file__).parent / ".cache"


@register(
    "picstatus",
    "Codex",
    "以图片形式显示当前设备的运行状态（AstrBot 版）",
    "1.0.0",
)
class PicStatusPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        ensure_dir(CACHE_DIR)
        self.config = config

    async def initialize(self):
        logger.info("PicStatus plugin initialized")

    @filter.command("运行状态", alias=ALIASES)
    async def cmd_status(self, event: AstrMessageEvent):
        """生成并发送当前服务器运行状态图片"""
        try:
            collected = await collect_all()
            collected.setdefault("nonebot_version", "AstrBot")
            collected.setdefault("ps_version", "v1.0.0")
            # Provide header bots info for template compatibility
            try:
                # 1) Bot 昵称优先顺序：插件配置 avatar_text > 环境变量 > AstrBot
                bot_nick = None
                if isinstance(getattr(self, "config", None), dict):
                    bot_nick = self.config.get("avatar_text") or None
                if not bot_nick:
                    bot_nick = (
                        os.getenv("PICSTATUS_BOT_NICK")
                        or os.getenv("NICKNAME")
                        or "AstrBot"
                    )

                self_id = event.get_self_id()
                adapter = (event.get_platform_name() or "AstrBot")

                bots = [
                    {
                        "self_id": self_id,
                        "nick": bot_nick,
                        "adapter": adapter,
                        "bot_connected": collected.get("nonebot_run_time", ""),
                        "msg_rec": 0,
                        "msg_sent": 0,
                    }
                ]
            except Exception:
                bots = []
            collected.setdefault("bots", bots)

            # prefer user image in message chain
            bg_bytes = None
            try:
                for seg in event.get_messages():
                    if isinstance(seg, Comp.Image):
                        f = getattr(seg, "file", None) or ""
                        if isinstance(f, str) and f.startswith(("http://", "https://")):
                            import httpx

                            with httpx.Client(follow_redirects=True, timeout=5) as cli:
                                r = cli.get(f)
                                r.raise_for_status()
                                bg_bytes = r.content
                                break
            except Exception:
                pass

            provider = os.getenv("PICSTATUS_BG_PROVIDER", "loli")
            local_path = os.getenv("PICSTATUS_BG_LOCAL_PATH")
            resolved = await resolve_background(
                prefer_bytes=bg_bytes,
                provider=provider,
                local_path=Path(local_path) if local_path else None,
            )
            # Renderer selection
            renderer_mode = os.getenv("PICSTATUS_RENDERER", "auto").lower()
            use_astr_t2i = renderer_mode in ("auto", "astr_t2i")
            use_html = renderer_mode in ("auto", "html", "html_strict")

            out_path = None
            html_error: Exception | None = None
            # Try AstrBot t2i first (if selected)
            t2i_error: Exception | None = None
            if use_astr_t2i and out_path is None:
                try:
                    from .t2i_renderer import build_default_html
                    import httpx

                    # 尝试获取 Bot 头像：PICSTATUS_BOT_AVATAR_URL > QQ qlogo（若平台为 AIoCQHTTP/QQ）
                    avatar_bytes = None
                    # 配置优先：avatar_path 可为 URL 或相对/绝对路径
                    avatar_url = None
                    avatar_path_cfg = None
                    if isinstance(getattr(self, "config", None), dict):
                        avatar_path_cfg = (self.config.get("avatar_path") or "").strip()
                    if avatar_path_cfg:
                        if avatar_path_cfg.startswith("http://") or avatar_path_cfg.startswith("https://"):
                            avatar_url = avatar_path_cfg
                        else:
                            p = Path(avatar_path_cfg)
                            if not p.is_absolute():
                                p = Path(__file__).parent / avatar_path_cfg
                            if p.exists():
                                try:
                                    avatar_bytes = p.read_bytes()
                                except Exception:
                                    avatar_bytes = None
                    # 环境变量次之
                    if (avatar_bytes is None) and (avatar_url is None):
                        avatar_url = os.getenv("PICSTATUS_BOT_AVATAR_URL")
                    if not avatar_url:
                        try:
                            if "qq" in adapter.lower() or "aiocqhttp" in adapter.lower():
                                avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={self_id}&s=640"
                        except Exception:
                            pass
                    if avatar_url:
                        try:
                            with httpx.Client(follow_redirects=True, timeout=5) as cli:
                                r = cli.get(avatar_url)
                                r.raise_for_status()
                                avatar_bytes = r.content
                        except Exception:
                            avatar_bytes = None

                    html = build_default_html(collected, resolved.data, avatar_bytes=avatar_bytes)
                    # Use AstrBot built-in html_render
                    options = {"type": "jpeg", "quality": 90, "full_page": True}
                    out_url = await self.html_render(html, {}, return_url=True, options=options)
                    # Prefer URL to avoid adapter local-file limitations
                    out_path = None
                    image_to_send = out_url
                    logger.info("PicStatus: AstrBot t2i renderer used")
                except Exception as e:
                    t2i_error = e
                    logger.warning(f"PicStatus: AstrBot t2i renderer unavailable, reason: {e}")

            if use_html and (out_path is None) and ('image_to_send' not in locals()):
                try:
                    from .html_renderer import render_html_image

                    img_bytes = await render_html_image(collected, resolved.data)
                    out_path = CACHE_DIR / "status.jpg"
                    out_path.write_bytes(img_bytes)
                    logger.info("PicStatus: HTML renderer used")
                except Exception as e:
                    html_error = e
                    logger.warning(f"PicStatus: HTML renderer unavailable, reason: {e}")
                    if renderer_mode == "html_strict":
                        raise

            if (out_path is None) and ('image_to_send' not in locals()):
                logger.info("PicStatus: fallback to Pillow renderer")
                out_path = render_status_pillow(collected, CACHE_DIR, bg_bytes=resolved.data)
        except Exception:
            logger.exception("生成运行状态图片失败")
            msg = "获取运行状态图片失败，请检查后台输出"
            if t2i_error:
                msg += "（AstrBot t2i 未就绪/模板渲染失败）"
            if html_error:
                msg += "（可能未安装 Playwright/Chromium：pip install playwright && python -m playwright install chromium）"
            yield event.plain_result(msg)
            return

        yield event.image_result(image_to_send if 'image_to_send' in locals() else str(out_path))

    async def terminate(self):
        logger.info("PicStatus plugin terminated")
