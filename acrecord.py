class AcRecord:
    def __init__(self, telegram_id: int):
        self.confirmed = False
        self.confirming = False
        self.telegram_id: int = telegram_id
        self.mw_id: str = ''
        self.confirmed_time: float = 0
        self.restricted_until: int = 0
        self.whitelist_reason: str = ''
        self.refused: bool = False

    def to_dict(self):
        return self.__dict__

    @classmethod
    def from_dict(cls, data: dict):
        obj = cls(data['telegram_id'])
        obj.confirmed = data['confirmed']
        obj.confirming = data['confirming']
        obj.mw_id = data['mw_id']
        obj.confirmed_time = data['confirmed_time']
        obj.restricted_until = data['restricted_until']
        obj.whitelist_reason = data['whitelist_reason']
        obj.refused = data['refused']

        return obj
