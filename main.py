from __future__ import annotations
from pathlib import Path
from typing import Final

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .collectors import collect_all
from .renderer import render_status
from .utils import ensure_dir


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
    def __init__(self, context: Context):
        super().__init__(context)
        ensure_dir(CACHE_DIR)

    async def initialize(self):
        logger.info("PicStatus plugin initialized")

    @filter.command("运行状态", alias=ALIASES)
    async def cmd_status(self, event: AstrMessageEvent):
        """生成并发送当前服务器运行状态图片"""
        try:
            collected = await collect_all()
            # add compatible keys expected by template texts
            collected.setdefault("nonebot_version", "AstrBot")
            out_path = render_status(collected, CACHE_DIR)
        except Exception:
            logger.exception("生成运行状态图片失败")
            yield event.plain_result("获取运行状态图片失败，请检查后台输出")
            return

        yield event.image_result(str(out_path))

    async def terminate(self):
        logger.info("PicStatus plugin terminated")
