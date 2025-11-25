# connection_record.py
class ConnectionRecord:
    def __init__(self, src: str, mcast_group: str, block_offset: int, block_size: int):
        self.src = src
        self.mcast_group = mcast_group
        self.block_offset = block_offset
        self.block_size = block_size