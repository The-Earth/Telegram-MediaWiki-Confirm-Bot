"""
Telegram 新成员欢迎消息的文本格式化工具
从 policr-mini 移植的 mosaic（马赛克）功能
只用于隐藏新成员加入时的用户名
"""

import unittest
from enum import Enum
from catbot.util import html_escape


class MosaicMethod(Enum):
    """马赛克方法"""
    SPOILER = "spoiler"      # 使用 <tg-spoiler> 隐藏标签（HTML 格式）
    CLASSIC = "classic"      # 使用 █ 方块符号


class TextFormatter:
    """文本格式化工具"""
    
    @staticmethod
    def mosaic_name(name: str, method: MosaicMethod = MosaicMethod.SPOILER) -> str:
        """
        对用户名进行马赛克处理
        """
        if not name:
            return ""
            
        name_len = len(name)
        
        # 1个字符 - 不处理
        if name_len == 1:
            return html_escape(name)
        
        # 2个字符
        if name_len == 2:
            if method == MosaicMethod.CLASSIC:
                return html_escape(name[0]) + "█"
            else:  # SPOILER
                first = html_escape(name[0])
                second = html_escape(name[1])
                return f"{first}<tg-spoiler>{second}</tg-spoiler>"
        
        # 3个字符及以上
        if method == MosaicMethod.CLASSIC:
            first = html_escape(name[0])
            last = html_escape(name[-1])
            
            # 3-5个字符按实际长度打码，6个以上固定 3 个方块
            if name_len <= 5:
                middle = "█" * (name_len - 2)
                return f"{first}{middle}{last}"
            else:
                return f"{first}███{last}"
                
        else:  # SPOILER
            first = html_escape(name[0])
            middle = html_escape(name[1:-1])
            last = html_escape(name[-1])
            return f"{first}<tg-spoiler>{middle}</tg-spoiler>{last}"
