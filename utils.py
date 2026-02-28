from catbot.util import html_escape

def partly_mosaic_name(name: str) -> str:
    """
    Telegram 对新用户名部分打马赛克
    从 policr-mini 移植的 mosaic（马赛克）功能
    只用于隐藏新成员加入时的用户名
    """
    if not name:
        return ""

    name_len = len(name)

    # 1个字符 - 不处理
    if name_len == 1:
        return html_escape(name)

    # 2个字符
    if name_len == 2:
        first = html_escape(name[0])
        second = html_escape(name[1])
        return f"{first}<tg-spoiler>{second}</tg-spoiler>"

    # 3个字符及以上
    first = html_escape(name[0])
    middle = html_escape(name[1:-1])
    last = html_escape(name[-1])
    return f"{first}<tg-spoiler>{middle}</tg-spoiler>{last}"
