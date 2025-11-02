import logging
from typing import Optional
from base_module import BaseModule, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, LedState

logger = logging.getLogger(__name__)

class ConnectionProtocol:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
                logger.info(f"{self.module_id}: Pending IO active, ignore INITIATE")
                return
            group = msg.payload.get('group', '')
            matched = False
            for io, info in self.inputs.items():
                if info.get('group') == group:
                    self.pending_io = io
                    self.led_states[io] = LedState.BLINK_SLOW
                    logger.info(f"{self.module_id}: Input {io} matched group {group}, blinking slow")
                    matched = True
                    # Trigger LED update chain
                    if self.root:
                        self.root.after(0, self._drain_queue_once)
                    break
            if not matched:
                logger.info(f"{self.module_id}: No input match for group {group}")

    def on_connect(self, msg: ProtocolMessage):
        with self.lock:
            if self.pending_io == msg.io_id:
                self.led_states[msg.io_id] = LedState.SOLID
                self.pending_io = None
                if msg.io_id in self.inputs:
                    self.inputs[msg.io_id]['src'] = f"{msg.module_id}:{msg.io_id}"
                    group = self.inputs[msg.io_id].get('group', msg.payload.get('group', ''))
                    if group:
                        self._start_receiver(msg.io_id, group)
                logger.info(f"{self.module_id}: Confirmed connect {msg.io_id} from {msg.module_id}")
                # Trigger LED update
                if self.root:
                    self.root.after(0, self._drain_queue_once)

    def on_cancel(self, msg: ProtocolMessage):
        with self.lock:
            io_id = msg.io_id
            if self.pending_io == io_id:
                self.led_states[io_id] = LedState.OFF
                self.pending_io = None
                if io_id in self.outputs:
                    self.initiating_outputs.discard(io_id)
                logger.info(f"{self.module_id}: Cancelled pending {io_id}")
            elif io_id in self.initiating_outputs:
                self.initiating_outputs.discard(io_id)
                self.led_states[io_id] = LedState.ERROR
                logger.warning(f"{self.module_id}: Error flash on cancel {io_id}")
                # 3x red flash stub
                if self.root:
                    self.root.after(200, lambda: self._flash_error(io_id))
            # Trigger LED update
            if self.root:
                self.root.after(0, self._drain_queue_once)

    def _flash_error(self, io_id):
        # Simple 3x flash (expand for full)
        for _ in range(3):
            self.led_states[io_id] = LedState.ERROR
            self.root.after(200, lambda: self._set_led_temp(io_id, 'red'))
            self.root.after(400, lambda: self._set_led_temp(io_id, 'gray'))

    def _set_led_temp(self, io, color):
        if io in self.gui_leds:
            self.gui_leds[io].config(background=color)
            self.root.after(0, self._drain_queue_once)

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
            # Trigger LED update
            if self.root:
                self.root.after(0, self._drain_queue_once)

    def connect_input(self, io_id: str):
        with self.lock:
            if io_id not in self.inputs or self.pending_io != io_id:
                return
            payload = {'group': self.inputs[io_id].get('group', '')}
            msg = ProtocolMessage(ProtocolMessageType.CONNECT.value, self.module_id, self.type, io_id, payload)
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            self.on_connect(msg)
            logger.info(f"{self.module_id}: Manual connect for input {io_id}")

    def cancel_connect(self, io_id: str):
        with self.lock:
            if self.pending_io == io_id:
                msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, self.type, io_id, {})
                self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                self.on_cancel(msg)
                logger.info(f"{self.module_id}: Cancelled pending {io_id}")