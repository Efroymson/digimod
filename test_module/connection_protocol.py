import logging
from typing import Optional, List, Tuple, Dict
import time
from enum import Enum

from base_module import BaseModule, ProtocolMessage, ProtocolMessageType, CONTROL_MULTICAST, UDP_CONTROL_PORT, LedState

logger = logging.getLogger(__name__)

def ip_to_tuple(ip_str: str) -> tuple:
    return tuple(int(octet) for octet in ip_str.split('.'))

class ConnectionProtocol:
    """
    Mixin for connection state machine and protocol handling.
    Handles INITIATE, CONNECT, CANCEL, COMPATIBLE, SHOW_CONNECTED messages.
    Manages states: 'initial', 'connection_pending', 'initiate_compatible'.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state = 'initial'  # initial, connection_pending, initiate_compatible
        self.pending_io: Optional[str] = None  # Pending output io_id (initiator side)
        self.pending_initiator: Optional[Tuple[str, str, str]] = None  # (mod_id, io_id, group) for receivers
        self.pending_unconn_inputs: List[str] = []  # Matching unconnected inputs lit SOLID (receivers)
        self.compatible_input: Optional[str] = None
        self.compatible_group: Optional[str] = None
        self.connected_inputs: Dict[str, List[Tuple[str, str]]] = {}  # output_io: list[(input_mod, input_io)]
        self.initiated_sent = False

    def handle_msg(self, msg: ProtocolMessage):
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
        if msg.module_id == self.module_id:
            return  # Ignore self
        if self.state == 'connection_pending':
            if not self.initiated_sent:
                logger.info(f"{self.module_id}: Refuse INITIATE: Own output pending, no sent")
                prior_state = self.led_states.get(self.pending_io, LedState.SOLID)
                self._flash_3x(self.pending_io, LedState.ERROR, prior_state)
                return
            # Race resolution
            msg_ip = msg.payload.get('ip', '255.255.255.255')
            msg_tuple = ip_to_tuple(msg_ip)
            my_tuple = ip_to_tuple(self.ip)
            if msg_tuple < my_tuple:
                logger.info(f"{self.module_id}: Lost race to {msg_ip} < {self.ip}")
                prior_state = self.led_states.get(self.pending_io, LedState.SOLID)
                self._flash_3x(self.pending_io, LedState.ERROR, prior_state)
                cancel_msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, self.type, self.pending_io, {})
                self.sock.sendto(cancel_msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                self._clear_pending()
                return
            logger.info(f"{self.module_id}: Won race over {msg_ip} > {self.ip}, ignore INITIATE")
            return
        # Receiver side: Light matching inputs if any, and turn off all outputs (system-wide effect)
        group = msg.payload.get('group', '')
        matching_unconn = [io for io, info in self.inputs.items() if info.get('group') == group and not info.get('src')]
        matching_conn = [io for io, info in self.inputs.items() if info.get('group') == group and info.get('src')]
        if not matching_unconn and not matching_conn:
            # Still turn off outputs even if no matching inputs
            for io in self.outputs:
                self._queue_led_update(io, LedState.OFF)
            return
        self.pending_initiator = (msg.module_id, msg.io_id, group)
        self.pending_unconn_inputs = matching_unconn
        for io in matching_unconn:
            self._queue_led_update(io, LedState.SOLID)
        # Note: matching_conn already BLINK_RAPID if connected
        # Turn off all outputs
        for io in self.outputs:
            self._queue_led_update(io, LedState.OFF)
        logger.info(f"{self.module_id}: Matching inputs lit for INITIATE from {msg.module_id}:{msg.io_id} (unconn: {len(matching_unconn)} )")

    def on_connect(self, msg: ProtocolMessage):
        payload = msg.payload or {}
        # Receiver-side clear: if this CONNECT matches our pending initiator
        if (self.pending_initiator and
            payload.get('output_mod') == self.pending_initiator[0] and
            payload.get('output_io') == self.pending_initiator[1]):
            for io in self.pending_unconn_inputs:
                self._queue_led_update(io, LedState.OFF)
            self.pending_initiator = None
            self.pending_unconn_inputs = []
            logger.info(f"{self.module_id}: Cleared pending inputs on CONNECT from {msg.module_id}:{msg.io_id}")
        # Initiator-side: add connection if matches pending
        if (self.state == 'connection_pending' and self.pending_io and
            payload.get('output_mod') == self.module_id and
            payload.get('output_io') == self.pending_io):
            input_mod = msg.module_id
            input_io = msg.io_id
            if self.pending_io not in self.connected_inputs:
                self.connected_inputs[self.pending_io] = []
            self.connected_inputs[self.pending_io].append((input_mod, input_io))
            self._clear_pending()
            logger.info(f"{self.module_id}: Connected {self.pending_io} to {input_mod}:{input_io}")

    def on_cancel(self, msg: ProtocolMessage):
        payload = msg.payload or {}
        if payload:  # Specific disconnect
            if payload.get('output_mod') == self.module_id:
                output_io = payload['output_io']
                input_mod = payload['input_mod']
                input_io = payload['input_io']
                if output_io in self.connected_inputs:
                    self.connected_inputs[output_io] = [
                        (im, ii) for im, ii in self.connected_inputs[output_io]
                        if (im, ii) != (input_mod, input_io)
                    ]
                    if not self.connected_inputs[output_io]:
                        del self.connected_inputs[output_io]
                # If this input was connected, update its LED (but since CANCEL from input long press)
                logger.info(f"{self.module_id}: Disconnected {input_mod}:{input_io} from {output_io}")
        else:  # Empty payload: clear pending from initiator CANCEL - system-wide: restore outputs to SOLID, clear inputs
            if self.pending_initiator:
                for io in self.pending_unconn_inputs:
                    self._queue_led_update(io, LedState.OFF)
                self.pending_initiator = None
                self.pending_unconn_inputs = []
                logger.info(f"{self.module_id}: Cleared pending inputs by CANCEL")
            # Restore all outputs to SOLID (system-wide)
            for io in self.outputs:
                self._queue_led_update(io, LedState.SOLID)
            if self.state == 'connection_pending':
                self._clear_pending()
                logger.info(f"{self.module_id}: Pending cleared by CANCEL")

    def on_compatible(self, msg: ProtocolMessage):
        group = msg.payload.get('group', '')
        if group == '':
            for io in self.outputs:
                self._queue_led_update(io, LedState.SOLID)
        else:
            for io, info in self.outputs.items():
                state = LedState.SOLID if info.get('group') == group else LedState.OFF
                self._queue_led_update(io, state)
        logger.info(f"{self.module_id}: Compatible LEDs updated for group '{group}'")

    def on_show_connected(self, msg: ProtocolMessage):
        found = False
        for output_io, conns in list(self.connected_inputs.items()):
            for input_mod, input_io in conns:
                if input_mod == msg.module_id and input_io == msg.io_id:
                    prior_state = self.led_states.get(output_io, LedState.SOLID)
                    self._flash_3x(output_io, LedState.ERROR, prior_state)
                    found = True
                    break
            if found:
                break
        if found:
            logger.info(f"{self.module_id}: Flashed connected output {output_io} 3x orange")

    def initiate_connect(self, io_id: str):
        """Handle output button press."""
        if io_id not in self.outputs:
            return
        current_state = self.led_states.get(io_id, LedState.OFF)
        if self.state == 'connection_pending':
            if io_id == self.pending_io:
                self.cancel_connect(io_id)
            else:
                self._flash_3x(io_id, LedState.ERROR, current_state)
            return
        if current_state != LedState.SOLID:
            self._flash_3x(io_id, LedState.ERROR, current_state)
            logger.info(f"{self.module_id}: Refused INITIATE for {io_id}: LED {current_state.name}")
            return
        # Proceed with initiate (covers compatible check implicitly, as incompatible would be OFF)
        payload = {'group': self.outputs[io_id]['group'], 'ip': self.ip}
        msg = ProtocolMessage(ProtocolMessageType.INITIATE.value, self.module_id, self.type, io_id, payload)
        self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
        self.pending_io = io_id
        self.state = 'connection_pending'
        self.initiated_sent = True
        self._queue_led_update(io_id, LedState.BLINK_SLOW)
        for other in self.outputs:
            if other != io_id:
                self._queue_led_update(other, LedState.OFF)
        logger.info(f"{self.module_id}: Sent INITIATE for {io_id}")

    def connect_input(self, io_id: str):
        """Handle input button press (short)."""
        if io_id not in self.inputs:
            return
        current_state = self.led_states.get(io_id, LedState.OFF)
        if self.inputs[io_id].get('src'):
            self.show_connected_press(io_id)
            return
        if self.state == 'connection_pending':
            if current_state == LedState.SOLID:  # Compatible (lit)
                # Proceed to connect
                if (self.pending_initiator and io_id in self.pending_unconn_inputs):
                    sender_mod, sender_io, group = self.pending_initiator
                    self.inputs[io_id]['src'] = f"{sender_mod}:{sender_io}"
                    if group:
                        self._start_receiver(io_id, group)
                    self._queue_led_update(io_id, LedState.BLINK_RAPID)
                    payload = {'output_mod': sender_mod, 'output_io': sender_io}
                    msg = ProtocolMessage(ProtocolMessageType.CONNECT.value, self.module_id, self.type, io_id, payload)
                    self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                    # Clear own pending (though this io now connected)
                    for other_io in self.pending_unconn_inputs:
                        if other_io != io_id:
                            self._queue_led_update(other_io, LedState.OFF)
                    self.pending_initiator = None
                    self.pending_unconn_inputs = []
                    logger.info(f"{self.module_id}: Connected {io_id} to {sender_mod}:{sender_io}")
                    return
            else:  # Incompatible (off)
                self._flash_3x(io_id, LedState.ERROR, current_state)
                logger.info(f"{self.module_id}: Refused input press for {io_id}: Incompatible during pending")
                return
        self.compatible_press(io_id)

    def compatible_press(self, io_id: str):
        """Handle unconnected input press in initial (send COMPATIBLE) or revert in compatible."""
        if self.state == 'connection_pending':
            logger.debug(f"{self.module_id}: Ignore input press during connection_pending")
            return
        if self.state == 'initiate_compatible':
            # Revert to initial
            payload = {'group': ''}
            msg = ProtocolMessage(ProtocolMessageType.COMPATIBLE.value, self.module_id, self.type, io_id, payload)
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            if self.compatible_input:
                self._queue_led_update(self.compatible_input, LedState.OFF)
            self.state = 'initial'
            self.compatible_input = None
            self.compatible_group = None
            logger.info(f"{self.module_id}: Reverted compatible state")
            return
        # Initial state, unconnected input
        if (self.state == 'initial' and not self.inputs[io_id].get('src') and
            io_id in self.inputs):
            group = self.inputs[io_id]['group']
            if group:
                payload = {'group': group}
                msg = ProtocolMessage(ProtocolMessageType.COMPATIBLE.value, self.module_id, self.type, io_id, payload)
                self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
                self._queue_led_update(io_id, LedState.BLINK_SLOW)
                self.compatible_input = io_id
                self.compatible_group = group
                self.state = 'initiate_compatible'
                logger.info(f"{self.module_id}: Sent COMPATIBLE for {io_id}, group {group}")

    def show_connected_press(self, io_id: str):
        """Short press on connected input: Send SHOW_CONNECTED."""
        if self.inputs[io_id].get('src'):
            msg = ProtocolMessage(ProtocolMessageType.SHOW_CONNECTED.value, self.module_id, self.type, io_id, {})
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            logger.info(f"{self.module_id}: Sent SHOW_CONNECTED for {io_id}")
        else:
            logger.warning(f"{self.module_id}: SHOW_CONNECTED ignored: {io_id} not connected")

    def long_press_input(self, io_id: str):
        """Long press on connected input: Disconnect."""
        if io_id in self.inputs and self.inputs[io_id].get('src'):
            src = self.inputs[io_id]['src']
            src_mod, src_io = src.split(':')
            self.inputs[io_id]['src'] = None
            self._queue_led_update(io_id, LedState.OFF)
            # Stop receiver if implemented (override in subclass if needed)
            payload = {
                'output_mod': src_mod,
                'output_io': src_io,
                'input_mod': self.module_id,
                'input_io': io_id
            }
            msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, self.type, io_id, payload)
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            logger.info(f"{self.module_id}: Long press canceled connection for {io_id}")
        else:
            logger.debug(f"{self.module_id}: Long press ignored: {io_id} not connected")

    def cancel_connect(self, io_id: str):
        """Press pending output: Cancel."""
        if self.state == 'connection_pending' and self.pending_io == io_id:
            msg = ProtocolMessage(ProtocolMessageType.CANCEL.value, self.module_id, self.type, io_id, {})
            self.sock.sendto(msg.pack(), (CONTROL_MULTICAST, UDP_CONTROL_PORT))
            self._clear_pending()
            logger.info(f"{self.module_id}: Canceled pending {io_id}")

    def _clear_pending(self):
        """Clear connection_pending state (initiator side)."""
        self.state = 'initial'
        if self.pending_io:
            self._queue_led_update(self.pending_io, LedState.SOLID)
        for io in self.outputs:
            self._queue_led_update(io, LedState.SOLID)
        for io in self.inputs:
            state = LedState.BLINK_RAPID if self.inputs[io].get('src') else LedState.OFF
            self._queue_led_update(io, state)
        self.pending_io = None
        self.initiated_sent = False
        logger.debug(f"{self.module_id}: Cleared pending state")

    def _flash_3x(self, io_id: str, on_state: LedState, prior_state: LedState, interval: int = 200):
        """Flash LED 3x (on-off-on-off-on-off) then revert. Uses ORANGE for error."""
        def pulse(phase: int):
            if phase >= 6:
                self._queue_led_update(io_id, prior_state)
                return
            state = on_state if phase % 2 == 0 else LedState.OFF
            self._queue_led_update(io_id, state)
            self.root.after(interval, lambda: pulse(phase + 1))
        pulse(0)