import time
import unittest

from hardware.rs485_ejector import (EjectorError, EjectorSettings, ModbusOutput,
                                     TimedEjector, crc16)


class FakeSerial:
    def __init__(self, *args, **kwargs):
        self.coils = [False] * 8
        self.response = b''
        self.closed = False
        self.writes = []

    def reset_input_buffer(self):
        self.response = b''

    def write(self, frame):
        assert crc16(frame[:-2]) == frame[-2:]
        self.writes.append(frame)
        address = frame[3]
        if frame[1] == 5:
            self.coils[address] = frame[4] == 0xFF
            self.response = frame
        elif frame[1] == 1:
            body = bytes((frame[0], 1, 1, int(self.coils[address])))
            self.response = body + crc16(body)
        elif frame[1] == 3:
            body = bytes((frame[0], 3, 2, 0, 0))
            self.response = body + crc16(body)
        return len(frame)

    def read(self, length):
        return self.response[:length]

    def close(self):
        self.closed = True


class WrongOnReadbackSerial(FakeSerial):
    def write(self, frame):
        count = super().write(frame)
        if frame[1] == 1 and self.coils[frame[3]]:
            body = bytes((frame[0], 1, 1, 0))
            self.response = body + crc16(body)
        return count


class EjectorTests(unittest.TestCase):
    def test_waveshare_command_and_readback(self):
        settings = EjectorSettings('COM9', 9600, 1, 1, 100, 30, True)
        fake = FakeSerial()
        output = ModbusOutput(settings, lambda *args, **kwargs: fake)
        output.open()
        output.set_output(True)
        self.assertEqual(fake.writes[-2], bytes.fromhex('01 05 00 00 FF 00 8C 3A'))
        output.close()
        self.assertFalse(fake.coils[0])

    def test_unset_decision_delay_cannot_arm(self):
        with self.assertRaises(EjectorError):
            EjectorSettings('COM9', decision_delay_ms=0, enabled=True).validate()

    def test_only_scheduled_ng_pulses(self):
        settings = EjectorSettings('COM9', decision_delay_ms=80, pulse_ms=30, enabled=True)
        fake = FakeSerial()
        events = []
        ejector = TimedEjector(ModbusOutput(settings, lambda *a, **k: fake),
                                lambda *event: events.append(event))
        ejector.start()
        decision=time.perf_counter()
        due=ejector.schedule(42, decision, 'FAIL_AB')
        self.assertAlmostEqual(due-decision, .080, places=4)
        deadline = time.perf_counter() + 1
        while len(events) < 2 and time.perf_counter() < deadline:
            time.sleep(.005)
        ejector.stop()
        self.assertEqual([event[0] for event in events], ['ON', 'OFF'])
        self.assertGreaterEqual(events[0][3]-decision, .075)
        self.assertFalse(fake.coils[0])

    def test_pass_cannot_schedule_output(self):
        settings=EjectorSettings('COM9', decision_delay_ms=100, enabled=True)
        fake=FakeSerial()
        ejector=TimedEjector(ModbusOutput(settings, lambda *a, **k: fake), lambda *e: None)
        ejector.start()
        try:
            with self.assertRaises(EjectorError):
                ejector.schedule(42, time.perf_counter(), 'PASS')
        finally:
            ejector.stop()
        self.assertFalse(fake.coils[0])

    def test_failed_on_readback_attempts_immediate_off(self):
        settings = EjectorSettings('COM9', decision_delay_ms=60,
                                   pulse_ms=30, enabled=True)
        fake = WrongOnReadbackSerial()
        events = []
        ejector = TimedEjector(ModbusOutput(settings, lambda *a, **k: fake),
                                lambda *event: events.append(event))
        ejector.start()
        ejector.schedule(7, time.perf_counter(), 'FAIL_OCR')
        deadline = time.perf_counter() + 1
        while not any(event[0] == 'ERROR' for event in events) and time.perf_counter() < deadline:
            time.sleep(.005)
        ejector.stop()
        self.assertTrue(any(event[0] == 'ERROR' for event in events))
        self.assertFalse(fake.coils[0])


if __name__ == '__main__':
    unittest.main()
