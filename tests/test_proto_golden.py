"""Protocol golden test: the wire format of proto.py must not change (UDP/Bluetooth compatibility).

The vectors were generated from proto.py as it was before the refactor. Every packet body is compared byte for byte,
the header / HMAC / v3 token layout is checked with an independent implementation, and the public constants
(type ids, flags, limits) are pinned so that renaming or renumbering one is caught here.

    python3 tests/test_proto_golden.py receiver
"""
import hmac
import struct
import sys

sys.path.insert(0, sys.argv[1])
import proto

PACKETS = {0: {'fmt': '!HBBH56s',
     'high': 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
     'low': '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
     'pattern': '0003101d002a05060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c',
     'size': 62,
     'wire': 0},
 1: {'fmt': '!6BbbHBB6i',
     'high': 'ffffffffffff7f7fffffffff7fffffff7fffffff7fffffff7fffffff7fffffff7fffffff',
     'low': '000000000000808000000000800000008000000080000000800000008000000080000000',
     'pattern': '03101d2a3744515e000714210000002e0000003b0000004800000055000000620000000b',
     'size': 36,
     'wire': 1},
 2: {'fmt': '!BBHHBBHH',
     'high': 'ffffffffffffffffffffffff',
     'low': '000000000000000000000000',
     'pattern': '0310001d002a37440051005e',
     'size': 12,
     'wire': 2},
 3: {'fmt': '!B', 'high': 'ff', 'low': '00', 'pattern': '03', 'size': 1, 'wire': 3},
 4: {'fmt': '!hhB', 'high': '7fff7fffff', 'low': '8000800000', 'pattern': '000300101d', 'size': 5, 'wire': 4},
 5: {'fmt': '!Q', 'high': 'ffffffffffffffff', 'low': '0000000000000000', 'pattern': '0000000000000003', 'size': 8, 'wire': 5},
 6: {'fmt': '!5B', 'high': 'ffffffffff', 'low': '0000000000', 'pattern': '03101d2a37', 'size': 5, 'wire': 6},
 7: {'fmt': '!Q', 'high': 'ffffffffffffffff', 'low': '0000000000000000', 'pattern': '0000000000000003', 'size': 8, 'wire': 7},
 8: {'fmt': '!', 'high': '', 'low': '', 'pattern': '', 'size': 0, 'wire': 8},
 9: {'fmt': '!17s',
     'high': 'ffffffffffffffffffffffffffffffffff',
     'low': '0000000000000000000000000000000000',
     'pattern': '0102030405060708090a0b0c0d0e0f1011',
     'size': 17,
     'wire': 9},
 10: {'fmt': '!BB', 'high': 'ffff', 'low': '0000', 'pattern': '0310', 'size': 2, 'wire': 10},
 11: {'fmt': '!HHH', 'high': 'ffffffffffff', 'low': '000000000000', 'pattern': '00030010001d', 'size': 6, 'wire': 11},
 12: {'fmt': '!BBB', 'high': 'ffffff', 'low': '000000', 'pattern': '03101d', 'size': 3, 'wire': 12},
 13: {'fmt': '!7sIB',
      'high': 'ffffffffffffffffffffffff',
      'low': '000000000000000000000000',
      'pattern': '01020304050607000000101d',
      'size': 12,
      'wire': 13},
 14: {'fmt': '!120s',
      'high': 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
      'low': '000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
      'pattern': '0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f606162636465666768696a6b6c6d6e6f707172737475767778',
      'size': 120,
      'wire': 14},
 15: {'fmt': '!4B', 'high': 'ffffffff', 'low': '00000000', 'pattern': '03101d2a', 'size': 4, 'wire': 15},
 257: {'fmt': '!6BbbHBB6iIHH',
       'high': 'ffffffffffff7f7fffffffff7fffffff7fffffff7fffffff7fffffff7fffffff7fffffffffffffffffffffff',
       'low': '0000000000008080000000008000000080000000800000008000000080000000800000000000000000000000',
       'pattern': '03101d2a3744515e000714210000002e0000003b0000004800000055000000620000000b0000001800250032',
       'size': 44,
       'wire': 1},
 263: {'fmt': '!QQ',
       'high': 'ffffffffffffffffffffffffffffffff',
       'low': '00000000000000000000000000000000',
       'pattern': '00000000000000030000000000000010',
       'size': 16,
       'wire': 7},
 265: {'fmt': '!HBB6sbB32s',
       'high': 'ffffffffffffffffffff7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
       'low': '0000000000000000000080000000000000000000000000000000000000000000000000000000000000000000',
       'pattern': '0003101d04050607080937440708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20212223242526',
       'size': 44,
       'wire': 9},
 266: {'fmt': '!B6s4sBH',
       'high': 'ffffffffffffffffffffffffffff',
       'low': '0000000000000000000000000000',
       'pattern': '03020304050607030405062a0037',
       'size': 14,
       'wire': 10},
 267: {'fmt': '!BB3i3iI',
       'high': 'ffff7fffffff7fffffff7fffffff7fffffff7fffffff7fffffffffffffff',
       'low': '000080000000800000008000000080000000800000008000000000000000',
       'pattern': '03100000001d0000002a0000003700000044000000510000005e00000007',
       'size': 30,
       'wire': 11},
 268: {'fmt': '!BBBBBHBB6s',
       'high': 'ffffffffffffffffffffffffffffff',
       'low': '000000000000000000000000000000',
       'pattern': '03101d2a370044515e090a0b0c0d0e',
       'size': 15,
       'wire': 12},
 269: {'fmt': '!7sIB6s4s',
       'high': 'ffffffffffffffffffffffffffffffffffffffffffff',
       'low': '00000000000000000000000000000000000000000000',
       'pattern': '01020304050607000000101d04050607080905060708',
       'size': 22,
       'wire': 13},
 271: {'fmt': '!12B',
       'high': 'ffffffffffffffffffffffff',
       'low': '000000000000000000000000',
       'pattern': '03101d2a3744515e0714212e',
       'size': 12,
       'wire': 15},
 521: {'fmt': '!HBBBB40s',
       'high': 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
       'low': '00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
       'pattern': '0003101d2a37060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d',
       'size': 46,
       'wire': 9},
 522: {'fmt': '!BB6s',
       'high': 'ffffffffffffffff',
       'low': '0000000000000000',
       'pattern': '0310030405060708',
       'size': 8,
       'wire': 10},
 527: {'fmt': '!12B11s11sB',
       'high': 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
       'low': '0000000000000000000000000000000000000000000000000000000000000000000000',
       'pattern': '03101d2a3744515e0714212e0d0e0f10111213141516170e0f10111213141516171855',
       'size': 35,
       'wire': 15},
 778: {'fmt': '!IB64s',
      'high': 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
      'low': '000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
      'pattern': '0a0b0c0d03000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f',
      'size': 69,
      'wire': 10},
 1034: {'fmt': '!IB960s',
       'high': 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
       'low': '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
       'pattern': '0a0b0c0d03000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f606162636465666768696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9fa0a1a2a3a4a5a6a7a8a9aaabacadaeafb0b1b2b3b4b5b6b7b8b9babbbcbdbebfc0c1c2c3c4c5c6c7c8c9cacbcccdcecfd0d1d2d3d4d5d6d7d8d9dadbdcdddedfe0e1e2e3e4e5e6e7e8e9eaebecedeeeff0f1f2f3f4f5f6f7f8f9fafbfcfdfeff000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f606162636465666768696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9fa0a1a2a3a4a5a6a7a8a9aaabacadaeafb0b1b2b3b4b5b6b7b8b9babbbcbdbebfc0c1c2c3c4c5c6c7c8c9cacbcccdcecfd0d1d2d3d4d5d6d7d8d9dadbdcdddedfe0e1e2e3e4e5e6e7e8e9eaebecedeeeff0f1f2f3f4f5f6f7f8f9fafbfcfdfeff000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f606162636465666768696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9fa0a1a2a3a4a5a6a7a8a9aaabacadaeafb0b1b2b3b4b5b6b7b8b9babbbcbdbebfc0c1c2c3c4c5c6c7c8c9cacbcccdcecfd0d1d2d3d4d5d6d7d8d9dadbdcdddedfe0e1e2e3e4e5e6e7e8e9eaebecedeeeff0f1f2f3f4f5f6f7f8f9fafbfcfdfeff000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f606162636465666768696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9fa0a1a2a3a4a5a6a7a8a9aaabacadaeafb0b1b2b3b4b5b6b7b8b9babbbcbdbebf',
       'size': 965,
       'wire': 10},
 780: {'fmt': '!6sbBBB',
       'high': 'ffffffffffffffffffff',
       'low': '00000000000000000000',
       'pattern': '0102030405060708090a',
       'size': 10,
       'wire': 12},
 781: {'fmt': '!7sIB6s4sB',
       'high': 'ffffffffffffffffffffffffffffffffffffffffffffff',
       'low': '0000000000000000000000000000000000000000000000',
       'pattern': '01020304050607000000101d0405060708090506070855',
       'size': 23,
       'wire': 13}}

CONSTANTS = {'BODY_LEN': {0: 62,
              1: 36,
              2: 12,
              3: 1,
              4: 5,
              5: 8,
              6: 5,
              7: 8,
              8: 0,
              9: 17,
              10: 2,
              11: 6,
              12: 3,
              13: 12,
              14: 120,
              15: 4,
              257: 44,
              263: 16,
              265: 44,
              266: 14,
              267: 30,
              268: 15,
              269: 22,
              271: 12,
              521: 46,
              522: 8,
              527: 35,
              778: 69,
              1034: 965,
              780: 10,
              781: 23},
 'BTA_AUTO': 1,
 'BTA_MANUAL': 3,
 'BTA_PICK': 2,
 'BTC_ACTIVE': 4,
 'BTC_BLOCKED': 8,
 'BTC_FIXED': 2,
 'BTC_PICKED': 16,
 'BTC_USB': 1,
 'BTD_CONNECTED': 2,
 'BTD_PAD': 8,
 'BTD_PAIRED': 1,
 'BTD_TRUSTED': 4,
 'BTF_BUSY': 2,
 'BTF_HAS_BUILTIN': 8,
 'BTF_HAS_USB': 16,
 'BTF_MODE_SHIFT': 6,
 'BTF_ON_USB': 32,
 'BTF_SCANNING': 1,
 'BTR_AUTH': 6,
 'BTR_BUSY': 9,
 'BTR_DENIED': 7,
 'BTR_FAILED': 3,
 'BTR_NONE': 0,
 'BTR_NOT_FOUND': 5,
 'BTR_OFF': 8,
 'BTR_OK': 2,
 'BTR_RUNNING': 1,
 'BTR_TIMEOUT': 4,
 'BTS_BLOCKED': 2,
 'BTS_DENIED': 5,
 'BTS_ERROR': 7,
 'BTS_NO_ADAPTER': 3,
 'BTS_NO_SERVICE': 4,
 'BTS_NO_TOOL': 6,
 'BTS_OFF': 1,
 'BTS_OK': 0,
 'BT_ACTIVE_S': 6.0,
 'BT_ADAPTER': 8,
 'BT_ADAPTER_USE': 9,
 'BT_CONNECT': 4,
 'BT_DEV_MAX': 32,
 'BT_DISCONNECT': 5,
 'BT_LIST': 0,
 'BT_PAIR': 3,
 'BT_POWER': 7,
 'BT_REMOVE': 6,
 'BTL_LQ': 2,
 'BTL_RSSI': 1,
 'BT_RSSI_NONE': -128,
 'BT_SCAN_MAX_S': 30,
 'BT_SCAN_START': 1,
 'BT_SCAN_STOP': 2,
 'BY_SIZE': [([0, 62], 0),
             ([1, 36], 1),
             ([1, 44], 257),
             ([2, 12], 2),
             ([3, 1], 3),
             ([4, 5], 4),
             ([5, 8], 5),
             ([6, 5], 6),
             ([7, 8], 7),
             ([7, 16], 263),
             ([8, 0], 8),
             ([9, 17], 9),
             ([9, 44], 265),
             ([9, 46], 521),
             ([10, 2], 10),
             ([10, 8], 522),
             ([10, 14], 266),
             ([10, 69], 778),
             ([10, 965], 1034),
             ([11, 6], 11),
             ([11, 30], 267),
             ([12, 3], 12),
             ([12, 10], 780),
             ([12, 15], 268),
             ([13, 12], 13),
             ([13, 22], 269),
             ([13, 23], 781),
             ([14, 120], 14),
             ([15, 4], 15),
             ([15, 12], 271),
             ([15, 35], 527)],
 'CALF_STORED': 1,
 'CAL_FEW': 2,
 'CAL_MAX_S': 10,
 'CAL_MOVED': 1,
 'CAL_OK': 0,
 'CAL_RUNNING': 4,
 'CAL_STATUS': 3,
 'CAP_CALIB': 16,
 'CAP_HAPTICS': 64,
 'CAP_SPEAKER': 128,
 'CAP_TRIGGERS': 4,
 'CAP_TRIG_FX': 8,
 'CAP_TRIG_RAW': 32,
 'CMD_GYRO_CAL': 3,
 'HAPT_CHUNK': 64,
 'HAPT_DATA_AT': 5,
 'HAPT_END': 2,
 'HAPT_FRAMES': 32,
 'HAPT_RATE': 3000,
 'HAPT_START': 1,
 'SPK_BYTES': 960,
 'SPK_DATA_AT': 5,
 'SPK_END': 2,
 'SPK_FRAME': 480,
 'SPK_RATE': 48000,
 'SPK_START': 1,
 'CMD_POWER_OFF': 1,
 'CMD_RAW_STICKS': 4,
 'CMD_UPDATE': 2,
 'DEPLOY_KEY': '.git/dualsense-deploy-key',
 'EV_ERROR': 2,
 'EV_INFO': 0,
 'EV_WARN': 1,
 'E_ERROR': 9,
 'E_NET_DOWN': 8,
 'E_NO_RECEIVER': 7,
 'E_NO_SLOT': 10,
 'E_PAD_OFF': 3,
 'E_PAD_ON': 2,
 'E_POWER_OFF': 4,
 'E_START': 1,
 'E_WOL': 5,
 'E_WOL_FAIL': 6,
 'FRESH_EXEMPT': [5],
 'FRESH_FUTURE_MS': 1000,
 'FRESH_PAST_MS': 3000,
 'GIT_PATHS': ('C:\\Program Files\\Git\\cmd\\git.exe', 'C:\\Program Files (x86)\\Git\\cmd\\git.exe'),
 'HDR0': 32,
 'HDR3': 48,
 'KEY_BIT': {304: 1, 305: 2, 307: 4, 308: 8, 310: 16, 311: 32, 314: 64, 315: 128, 316: 256, 317: 512, 318: 1024},
 'KEY_MISMATCH_S': 5.0,
 'KNOWN_HOSTS': '.git/dualsense-known-hosts',
 'LAT_NONE': 65535,
 'MAC_LEN': 8,
 'PAD_KEYS': (304, 305, 307, 308, 310, 311, 314, 315, 316, 317, 318),
 'RAW_STICKS_MAX_S': 30,
 'RFX_GAIN_MAX': 200,
 'RFX_HOLD_S': 0.3,
 'RFX_ON': 1,
 'RTT_NONE': 65535,
 'RUMBLE_EVDEV': 1,
 'RUMBLE_HIDRAW': 2,
 'SCFG_BATT': 4,
 'SCFG_EVENTS': 2,
 'SCFG_WOL': 1,
 'SENDER_VERSION': 14,
 'STATE_FIELDS': 17,
 'TFX_CLICK': 2,
 'TFX_OFF': 0,
 'TFX_RES': 1,
 'TFX_VIB': 3,
 'TRAILER3': 12,
 'TRG3_L2': 1,
 'TRG3_R2': 2,
 'TRIG2_OFF': (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
 'TRIG_AMP_MAX': 8,
 'TRIG_CLICK_MAX': 7,
 'TRIG_CLICK_MIN': 2,
 'TRIG_FORCE_MAX': 8,
 'TRIG_FREQ_MAX': 255,
 'TRIG_RAW_LEN': 11,
 'TRIG_START_MAX': 9,
 'T_BTCMD': 522,
 'T_BTCTRL': 521,
 'T_BTLINK': 780,
 'T_BTDEV': 265,
 'T_BTSTATE': 268,
 'T_BUILD': 13,
 'T_BUILD2': 269,
 'T_BUILD3': 781,
 'T_BYE': 8,
 'T_CALRES': 267,
 'T_CLICK': 3,
 'T_CMD': 10,
 'T_EVENT': 0,
 'T_FB': 6,
 'T_IDENT': 9,
 'T_INFO': 11,
 'T_MOUSE': 4,
 'T_HAPT': 778,
 'T_SPK': 1034,
 'T_PING': 5,
 'T_PONG': 7,
 'T_PONG2': 263,
 'T_SCFG': 266,
 'T_STATE': 1,
 'T_STATE2': 257,
 'T_TOUCH': 2,
 'T_TRIG': 15,
 'T_TRIG2': 271,
 'T_TRIG3': 527,
 'T_UPDMSG': 14,
 'T_VERSION': 12,
 'UPD_DONE': 3,
 'UPD_FAILED': 6,
 'UPD_NONE': 0,
 'UPD_NOT_GIT': 4,
 'UPD_NO_CHANGE': 2,
 'UPD_NO_GIT': 5,
 'UPD_RUNNING': 1,
 'VER': 2,
 'VER3': 3,
 'WHY_ACCESS': 2,
 'WHY_NOT_FOUND': 4,
 'WHY_NO_HIDRAW': 3,
 'WHY_OFF': 1,
 'WHY_OK': 0,
 'WHY_OPEN': 5,
 'WIRE': {257: 1, 263: 7, 265: 9, 266: 10, 267: 11, 268: 12, 269: 13, 271: 15, 521: 9, 522: 10, 527: 15, 778: 10, 780: 12, 781: 13, 1034: 10},
 'WOL_PORTS': (9, 7)}

HELPERS = {'mac_bytes': [['aa:bb:cc:dd:ee:ff', 'aabbccddeeff'],
               ['AA-BB-CC-DD-EE-FF', 'aabbccddeeff'],
               ['aabbccddeeff', 'aabbccddeeff'],
               ['00:00:00:00:00:00', None],
               ['aa:bb', None],
               ['', None],
               ['zz:bb:cc:dd:ee:ff', None]],
 'mac_text': 'aa:bb:cc:dd:ee:ff',
 'magic': 'ffffffffffffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeffaabbccddeeff',
 'trig2_legacy': [[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                  [1, 3, 5, 0, 1, 9, 8, 0, 0, 0, 0, 0],
                  [0, 0, 0, 0, 1, 2, 4, 0, 0, 0, 0, 0]],
 'utf8': [['7a61c5bcc3b3e280a6'], ['616263']]}


KEY = b"0123456789abcdef"
FIXED_TOKEN = 0xDEADBEEF
IDX = 7


def _packet_vectors():
    for ptype, spec in PACKETS.items():
        for name in ("pattern", "high", "low"):
            yield ptype, name, bytes.fromhex(spec[name])


def _unpack(ptype, body):
    return proto.BODY[ptype].unpack(body)


def check_body_layout():
    """Struct format, size and wire type of every packet; the body bytes of three value vectors."""
    assert set(proto.BODY) == {int(t) for t in PACKETS}, "a packet type was added or removed"
    for ptype, spec in PACKETS.items():
        st = proto.BODY[ptype]
        assert st.format == spec["fmt"], (ptype, st.format, spec["fmt"])
        assert st.size == spec["size"], (ptype, st.size)
        assert proto.WIRE.get(ptype, ptype) == spec["wire"], ptype
    for ptype, name, raw in _packet_vectors():
        assert proto.BODY[ptype].pack(*_unpack(ptype, raw)) == raw, (ptype, name)


def check_plain_packets():
    """Without a key: header "!BBH" (version 2 | wire type, idx, seq) followed by the body, nothing else."""
    codec = proto.Codec()
    for ptype, name, body in _packet_vectors():
        pkt = codec.pack(ptype, IDX, *_unpack(ptype, body))
        assert pkt[0] == (2 << 4) | PACKETS[ptype]["wire"], (ptype, pkt[:1].hex())
        assert pkt[1] == IDX
        assert pkt[4:] == body, (ptype, name)
        assert len(pkt) == 4 + len(body)
        assert codec.parse(pkt) == (ptype, IDX, (pkt[2] << 8) | pkt[3]), (ptype, name)
    seqs = [codec.pack(proto.T_CLICK, 0, 1)[2:4] for _ in range(3)]
    values = [int.from_bytes(s, "big") for s in seqs]
    assert [(b - a) & 0xFFFF for a, b in zip(values, values[1:], strict=False)] == [1, 1], "sequence must increase by one"


def check_signed_v2_packets():
    """With a key, before the switch to v3: header 2 + body + 8 B of truncated HMAC-SHA256 over everything before it."""
    sender = proto.Codec(KEY)
    receiver = proto.Codec(KEY)
    for ptype, name, body in _packet_vectors():
        pkt = sender.pack(ptype, IDX, *_unpack(ptype, body), v3=False)
        msg, tag = pkt[:-8], pkt[-8:]
        assert pkt[0] >> 4 == 2 and msg[4:] == body, (ptype, name)
        assert tag == hmac.digest(KEY, msg, "sha256")[:8], (ptype, name)
        assert receiver.parse(pkt) == (ptype, IDX, (pkt[2] << 8) | pkt[3]), (ptype, name)


def check_signed_v3_packets():
    """v3: header 3 + body + "!I" freshness token + 8 B of HMAC computed over header, body and token."""
    sender = proto.Codec(KEY, token=lambda: FIXED_TOKEN)
    for ptype, name, body in _packet_vectors():
        pkt = sender.pack(ptype, IDX, *_unpack(ptype, body), v3=True)
        msg, tag = pkt[:-8], pkt[-8:]
        assert pkt[0] >> 4 == 3 and pkt[0] & 0x0F == PACKETS[ptype]["wire"], ptype
        assert msg[4:-4] == body, (ptype, name)
        assert struct.unpack("!I", msg[-4:])[0] == FIXED_TOKEN
        assert tag == hmac.digest(KEY, msg, "sha256")[:8], (ptype, name)
    no_token = proto.Codec(KEY)
    assert no_token.pack(proto.T_CLICK, 0, 1, v3=True) is None, "v3 without a token for the peer must not go out"
    probe = no_token.pack(proto.T_PING, 0, 1, v3=True)
    assert probe is not None and struct.unpack("!I", probe[-12:-8])[0] == 0, "a ping goes out with token 0"


def check_rejections():
    """Wrong key, truncated packets, unknown versions and unknown lengths are all dropped."""
    good = proto.Codec(KEY).pack(proto.T_CLICK, 0, 1, v3=False)
    assert proto.Codec(b"another key 1234").parse(good) is None
    assert proto.Codec(KEY).parse(good[:-1]) is None
    assert proto.Codec().parse(b"\x00\x00\x00") is None
    plain = proto.Codec().pack(proto.T_CLICK, 0, 1)
    assert proto.Codec().parse(bytes([(9 << 4) | (plain[0] & 0x0F)]) + plain[1:]) is None, "unknown version"
    assert proto.Codec().parse(plain + b"\x00") is None, "unknown body length"


def _canonical(name, value):
    """A comparable, JSON-like form of a proto constant."""
    if name == "BY_SIZE":
        return sorted(([int(a), int(b)], int(v)) for (a, b), v in value.items())
    if isinstance(value, dict):
        return {int(k): int(v) for k, v in value.items()}
    if isinstance(value, frozenset):
        return sorted(value)
    return value


def check_constants():
    """Every public constant keeps its name and value: other modules (and older peers) depend on them."""
    for name, expected in CONSTANTS.items():
        if name == "BY_SIZE":
            expected = sorted(([a, b], v) for (a, b), v in expected)
        actual = _canonical(name, getattr(proto, name))
        assert actual == expected, f"{name}: {actual!r} != {expected!r}"


def check_helpers():
    """Pure helpers that build or decode packet fields."""
    for text, expected in HELPERS["mac_bytes"]:
        got = proto.mac_bytes(text)
        assert (got.hex() if got else None) == expected, text
    assert proto.mac_text(bytes.fromhex("aabbccddeeff")) == HELPERS["mac_text"]
    assert proto.magic_packet(bytes.fromhex("aabbccddeeff")).hex() == HELPERS["magic"]
    for legacy, expected in zip([(0, 0, 0, 0), (3, 5, 9, 8), (1, 0, 2, 4)], HELPERS["trig2_legacy"], strict=True):
        assert list(proto.trig2_from_legacy(legacy)) == expected, legacy
    assert proto.utf8_field("zażółć gęślą jaźń", 9, "…").hex() == HELPERS["utf8"][0][0]
    assert proto.utf8_field("abc", 8).hex() == HELPERS["utf8"][1][0]


def check_freshness_window():
    """The v3 token window: FRESH_PAST_MS in the past, FRESH_FUTURE_MS in the future, modulo 2**32."""
    now = 1_000_000
    assert proto.token_fresh(now - proto.FRESH_PAST_MS, now)
    assert not proto.token_fresh(now - proto.FRESH_PAST_MS - 1, now)
    assert proto.token_fresh(now + proto.FRESH_FUTURE_MS, now)
    assert not proto.token_fresh(now + proto.FRESH_FUTURE_MS + 1, now)
    assert proto.token_fresh(0xFFFFFFFF, 5), "a token just before the 32-bit wrap-around is still in the past"


for check in (check_body_layout, check_plain_packets, check_signed_v2_packets, check_signed_v3_packets, check_rejections,
              check_constants, check_helpers, check_freshness_window):
    check()
print("PROTO GOLDEN OK")
