# connection_protocol.py
import time
import threading
import logging
from typing import Optional
from base_module import BaseModule, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, LedState, RECV_TIMEOUT

logger = logging.getLogger(__name__)

class ConnectionProtocol:
    def __init__(self):
        self.pending_io: Optional[str] = None
        self.initiating_outputs = set()

    def handle_msg(self, msg: ProtocolMessage):
        with self.lock:
            if msg.type == ProtocolMessageType.INITIATE.value:
                self.on_initiate(msg)
            elif msg.type == ProtocolMessageType.CONNECT.value:
                self.on_connect(msg)
            elif msg.type == ProtocolMessageType.CANCEL.value:
                self.on_cancel(msg)
        super().handle_msg(msg)

    def on_initiate(self, msg: ProtocolMessage):
        with self.lock:
            if self.pending_io:
                return
            group = msg.payload.get('group', '')
            for io, info in self.inputs.items():
                if info.get('group') == group:
                    self.pending_io = io
                    self.led_states[io] = LedState.BLINK_SLOW
                    logger.info(f"{self.module_id}: Input {io} matched group {group}, blinking")
                    return
            logger.info(f"{self.module_id}: No input match for group {group}")

    def on_connect(self, msg: ProtocolMessage):
        with self.lock:
            if self.pending_io == msg.io_id:
                self.led_states[msg.io_id] = LedState.SOLID
                self.pending_io = None
                if msg.io_id in self.inputs:
                    self.inputs[msg.io_id]['src'] = f"{msg.module_id}:{msg.io_id}"
                    group = self.inputs[msg.io_id]['group']
                    self._start_receiver(msg.io_id, group)
                logger.info(f"{self.module_id}: Confirmed connect {msg.io_id}")

    def on_cancel(self, msg: ProtocolMessage):
        with self.lock:
            if self.pending_io == msg.io_id:
                self.led_states[msg.io_id] = LedState.OFF
                self.pending_io = None
                if msg.io_id in self.outputs:
                    self.initiating_outputs.discard(msg.io_id)
                logger.info(f"{self.module_id}: Cancelled {msg.io_id}")

    def initiate_connect(self, io_id: str):
        with self.lock:
            if io_id not in self.outputs:
                return
            if io_id in self.initiating_outputs:
                self.led_states[io_id] = LedState.OFF
                self.pending_io = None
                self.initiating_outputs.remove(io_id)
                msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, self.type, io_id, {})
                self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                logger.info(f"{self.module_id}: De-selected output {io_id}, broadcast CANCEL")
            else:
                self.initiating_outputs.add(io_id)
                self.pending_io = io_id
                self.led_states[io_id] = LedState.BLINK_SLOW
                payload = {'group': self.outputs[io_id].get('group', '')}
                msg = ProtocolMessage(ProtocolMessageType.INITIATE.value, self.module_id, self.type, io_id, payload)
                self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                logger.info(f"{self.module_id}: Initiated connect for {io_id}, broadcast INITIATE with group {payload['group']}")

    def connect_input(self, io_id: str):
        with self.lock:
            if io_id not in self.inputs or self.pending_io != io_id:
                return
            payload = {'group': self.inputs[io_id].get('group', '')}
            msg = ProtocolMessage(ProtocolMessageType.CONNECT.value, self.module_id, self.type, io_id, payload)
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            self.on_connect(msg)
            logger.info(f"{self.module_id}: Manual connect for input {io_id}")