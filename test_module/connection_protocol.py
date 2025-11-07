import logging
from typing import Optional, List, Tuple
import time
from base_module import BaseModule, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, LedState

logger = logging.getLogger(__name__)

def ip_to_tuple(ip_str: str) -> tuple:
    return tuple(int(octet) for octet in ip_str.split('.'))

class ConnectionProtocol:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state = 'initial'  # initial, connection_pending, initiate_compatible
        self.pending_io: Optional[str] = None
        self.pending_ios: List[Tuple[str, str]] = []  # (io, sender_mod)
        self.initiating_outputs = set()
        self.initiated_sent = False
        self.compatible_input = None  # Pressed input in initiate_compatible
        self.last_push_time = {}

    def handle_msg(self, msg: ProtocolMessage):
        # No lock: Distributed
        if msg.type == ProtocolMessageType.INITIATE.value:
            self.on_initiate(msg)
        elif msg.type == ProtocolMessageType.CONNECT.value:
            self.on_connect(msg)
        elif msg.type == ProtocolMessageType.CANCEL.value:
            self.on_cancel(msg)
        elif msg.type == ProtocolMessageType.COMPATIBLE.value:
            self.on_compatible(msg)
        elif msg.type == ProtocolMessageType.SHOW_CONNECTED.value:
            self.on_show_connected(msg)
        super().handle_msg(msg)

    def on_initiate(self, msg: ProtocolMessage):
        # No lock: Atomic
        if msg.module_id == self.module_id:
            return
        if self.state == 'connection_pending' and self.initiating_outputs:
            if not self.initiated_sent:
                logger.info(f"{self.module_id}: Refuse INITIATE: Own output pending, no sent")
                self._flash_error(list(self.initiating_outputs)[0])
                return
            # Race
            msg_ip = msg.payload.get('ip', '255.255.255.255')
            msg_tuple = ip_to_tuple(msg_ip)
            my_tuple = ip_to_tuple(self.ip)
            if msg_tuple < my_tuple:
                logger.info(f"{self.module_id}: Lost race to {msg_ip} < {self.ip}, flashing error")
                self._flash_error(list(self.initiating_outputs)[0])
                cancel_msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, self.type, list(self.initiating_outputs)[0], {})
                self.sock.sendto(cancel_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                self.initiating_outputs.clear()
                self.pending_io = None
                self.pending_ios = []
                self.initiated_sent = False
                self.state = 'initial'
                return
            logger.info(f"{self.module_id}: Won race over {msg_ip} > {self.ip}, ignore INITIATE")
            return
        if self.pending_ios:
            logger.info(f"{self.module_id}: Refuse INITIATE: Input pending")
            return
        group = msg.payload.get('group', '')
        matching_ios = [(io, msg.module_id) for io, info in self.inputs.items() if info.get('group') == group]
        if matching_ios:
            self.pending_ios = matching_ios
            self.pending_io = matching_ios[0][0]
            for io, _ in matching_ios:
                self._queue_led_update(io, LedState.BLINK_RAPID)
            logger.info(f"{self.module_id}: Inputs matched, blinking rapid")
        else:
            logger.info(f"{self.module_id}: No input match")

    def on_connect(self, msg: ProtocolMessage):
        # No lock: Atomic
        if self.pending_io == msg.io_id:
            self._queue_led_update(msg.io_id, LedState.SOLID)
            for io, _ in self.pending_ios:
                if io != msg.io_id:
                    self._queue_led_update(io, LedState.OFF)
            self.pending_io = None
            self.pending_ios = []
            self.initiated_sent = False
            self.state = 'initial'
            if msg.io_id in self.inputs:
                self.inputs[msg.io_id]['src'] = f"{msg.module_id}:{msg.io_id}"
                group = self.inputs[msg.io_id].get('group', msg.payload.get('group', ''))
                if group:
                    self._start_receiver(msg.io_id, group)
            logger.info(f"{self.module_id}: Confirmed connect {msg.io_id}")

    def on_cancel(self, msg: ProtocolMessage):
        # No lock: Atomic
        io_id = msg.io_id
        if self.pending_io == io_id:
            self._queue_led_update(io_id, LedState.OFF)
            for io, _ in self.pending_ios:
                self._queue_led_update(io, LedState.OFF)
            self.pending_io = None
            self.pending_ios = []
            self.initiated_sent = False
            self.state = 'initial'
            if io_id in self.outputs:
                self.initiating_outputs.discard(io_id)
            logger.info(f"{self.module_id}: Cancelled pending {io_id}")
        elif io_id in self.initiating_outputs:
            self.initiating_outputs.discard(io_id)
            self._queue_led_update(io_id, LedState.OFF)
            self.state = 'initial'
            logger.warning(f"{self.module_id}: Error on cancel {io_id}")

    def on_compatible(self, msg: ProtocolMessage):
        # No lock: Atomic
        group = msg.payload.get('group', '')
        compatible = [io for io, info in self.outputs.items() if info.get('group') == group]
        for io in self.outputs:
            state = LedState.SOLID if io in compatible else LedState.OFF
            self._queue_led_update(io, state)
        logger.info(f"{self.module_id}: Compatible outputs {compatible} lit for group {group}")

    def on_show_connected(self, msg: ProtocolMessage):
        # No lock: Atomic
        io_id = msg.payload.get('io_id', '')
        if io_id in self.outputs:
            prior_state = self.led_states.get(io_id, LedState.OFF)
            self._flash_error(io_id, 1200)  # 3x 200ms orange, revert prior
            logger.info(f"{self.module_id}: Flashed connected output {io_id}")

    def initiate_connect(self, io_id: str):
        # No lock: Atomic
        if self.state == 'initial' or self.state == 'initiate_compatible':
            if io_id in self.initiating_outputs:
                self.pending_io = None
                self.pending_ios = []
                self.initiated_sent = False
                self.initiating_outputs.remove(io_id)
                msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, self.type, io_id, {})
                self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                self._queue_led_update(io_id, LedState.OFF)
                logger.info(f"{self.module_id}: De-selected output {io_id}, broadcast CANCEL")
            else:
                self.initiating_outputs.add(io_id)
                self.pending_io = io_id
                self.initiated_sent = True
                payload = {'group': self.outputs[io_id].get('group', ''), 'ip': self.ip}
                msg = ProtocolMessage(ProtocolMessageType.INITIATE.value, self.module_id, self.type, io_id, payload)
                self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                self._queue_led_update(io_id, LedState.BLINK_SLOW)
                self.state = 'connection_pending'
                logger.info(f"{self.module_id}: Initiated connect for {io_id}, go to connection_pending")
        else:
            self._flash_error(io_id)  # 3x orange, ignore
            logger.info(f"{self.module_id}: Ignore press in state {self.state}")

    def connect_input(self, io_id: str):
        # No lock: Atomic
        if self.state == 'connection_pending' and io_id in self.pending_ios:
            sender_mod = next((sender for io, sender in self.pending_ios if io == io_id), "initiator_mod")
            self.inputs[io_id]['src'] = f"{sender_mod}:{io_id}"
            group = self.inputs[io_id].get('group', '')
            if group:
                self._start_receiver(io_id, group)
            self._queue_led_update(io_id, LedState.SOLID)
            for io, _ in self.pending_ios:
                if io != io_id:
                    self._queue_led_update(io, LedState.OFF)
            self.pending_io = None
            self.pending_ios = []
            self.initiated_sent = False
            self.state = 'initial'
            logger.info(f"{self.module_id}: Connected input {io_id}, back to initial")
        else:
            logger.warning(f"{self.module_id}: Ignore input press in state {self.state}")

    def compatible_press(self, io_id: str):
        # No lock: Atomic
        if self.state == 'initial':
            payload = {'group': self.inputs[io_id].get('group', '')}
            msg = ProtocolMessage(ProtocolMessageType.COMPATIBLE.value, self.module_id, self.type, io_id, payload)
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            self._queue_led_update(io_id, LedState.BLINK_SLOW)
            self.compatible_input = io_id
            self.state = 'initiate_compatible'
            logger.info(f"{self.module_id}: Compatible press for {io_id}, go to initiate_compatible")
        else:
            self.state = 'initial'
            self._queue_led_update(io_id, LedState.OFF)
            logger.info(f"{self.module_id}: Input press back to initial")

    def show_connected_press(self, io_id: str):
        # No lock: Atomic
        if self.state == 'connection_pending' and io_id in self.inputs and self.inputs[io_id].get('src'):
            src = self.inputs[io_id]['src']
            src_mod, src_io = src.split(':')
            payload = {'io_id': io_id}
            msg = ProtocolMessage(ProtocolMessageType.SHOW_CONNECTED.value, self.module_id, self.type, io_id, payload)
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            logger.info(f"{self.module_id}: Short press on connected {io_id}, SHOW_CONNECTED to {src_mod}")
        elif self.state == 'initial' and io_id in self.inputs and self.inputs[io_id].get('src'):
            # Long press cancel
            self.inputs[io_id]['src'] = None
            self._queue_led_update(io_id, LedState.SOLID)  # Green solid (stop blink, stay connected? Spec: "go solid"—assume stop blink)
            logger.info(f"{self.module_id}: Long press on connected {io_id}, stop blink")
        else:
            logger.warning(f"{self.module_id}: Ignore press on {io_id}")

    def cancel_connect(self, io_id: str):
        # No lock: Atomic
        if self.state == 'connection_pending' and self.pending_io == io_id:
            msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, self.type, io_id, {})
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            self.on_cancel(msg)
            logger.info(f"{self.module_id}: Cancelled pending {io_id}")

    def _flash_error(self, io_id: str):
        def pulse(n):
            if n > 0:
                state = LedState.ERROR if n % 2 == 1 else LedState.OFF
                self._queue_led_update(io_id, state)
                self.root.after(200, lambda: pulse(n - 1))
            else:
                self._queue_led_update(io_id, LedState.OFF)
        pulse(6)

    def _flash_connected_output(self, io_id: str):
        # 3x orange flash, revert prior (timeout pattern)
        prior_state = self.led_states.get(io_id, LedState.SOLID)
        prior_color = 'green'  # Assume SOLID
        self._queue_led_update(io_id, LedState.ERROR)  # Orange
        self.root.after(1200, lambda: self._queue_led_update(io_id, prior_state))  # Revert after 1.2s
        logger.info(f"{self.module_id}: Flashed connected output {io_id} 3x orange")