"""
Telegram 新成员欢迎消息的文本格式化工具
从 policr-mini 移植的 mosaic（马赛克）功能
只用于隐藏新成员加入时的用户名
"""

import unittest
from enum import Enum


class MosaicMethod(Enum):
    """马赛克方法"""
    SPOILER = "spoiler"      # 使用 <tg-spoiler> 隐藏标签（HTML 格式）
    CLASSIC = "classic"      # 使用 █ 方块符号


class TextFormatter:
    """文本格式化工具"""
    
    @staticmethod
    def safe_html(text: str) -> str:
        """转义 HTML 特殊字符"""
        escape_table = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }
        return ''.join(escape_table.get(c, c) for c in text)
    
    @staticmethod
    def mosaic_name(name: str, method: MosaicMethod = MosaicMethod.SPOILER) -> str:
        """
        对用户名进行马赛克处理
        
        规则：
        - 1个字符：不处理 → "A"
        - 2个字符：隐藏第二个 → "A<tg-spoiler>B</tg-spoiler>"
        - 3-5个字符：隐藏中间 → "A<tg-spoiler>BCD</tg-spoiler>E"
        - 6个以上：隐藏中间 → "A<tg-spoiler>BCDEFG</tg-spoiler>H"
        
        参数:
            name: 用户名
            method: 使用 SPOILER（推荐）或 CLASSIC 方法
        
        返回值:
            处理后的 HTML 格式名字
            
        使用例子:
            >>> mosaic_name("小明")
            "小<tg-spoiler>明</tg-spoiler>"
            >>> mosaic_name("Hello")
            "H<tg-spoiler>ell</tg-spoiler>o"
            >>> mosaic_name("Hentioe")
            "H<tg-spoiler>entio</tg-spoiler>e"
        """
        # 防御：处理空字符串
        if not name:
            return ""
            
        name_len = len(name)
        
        # 1个字符 - 不处理
        if name_len == 1:
            return TextFormatter.safe_html(name)
        
        # 2个字符
        if name_len == 2:
            if method == MosaicMethod.CLASSIC:
                return TextFormatter.safe_html(name[0]) + "█"
            else:  # SPOILER
                first = TextFormatter.safe_html(name[0])
                second = TextFormatter.safe_html(name[1])
                return f"{first}<tg-spoiler>{second}</tg-spoiler>"
        
        # 3个字符及以上
        if method == MosaicMethod.CLASSIC:
            first = TextFormatter.safe_html(name[0])
            last = TextFormatter.safe_html(name[-1])
            
            # 3-5个字符按实际长度打码，6个以上固定 3 个方块
            if name_len <= 5:
                middle = "█" * (name_len - 2)
                return f"{first}{middle}{last}"
            else:
                return f"{first}███{last}"
                
        else:  # SPOILER
            first = TextFormatter.safe_html(name[0])
            middle = TextFormatter.safe_html(name[1:-1])
            last = TextFormatter.safe_html(name[-1])
            return f"{first}<tg-spoiler>{middle}</tg-spoiler>{last}"