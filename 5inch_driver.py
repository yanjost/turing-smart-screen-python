import binascii
import time
from math import ceil
from typing import Union, Optional
import sys

import serial
import cv2
import atexit
import enum
import selectolax.lexbor as lex
from loguru import logger

logger.add(sys.stdout, colorize=True, format="<green>{time}</green> <level>{message}</level>")

from html2image import Html2Image

hti = Html2Image(size=(800, 480))

SCREEN_HEIGHT = 800

INPUT_MESSAGE_SIZE = 1024

MESSAGE_MAX_SIZE = 250

FAIL_ON_EXPECT = True


class CMD(enum.Enum):
    Get_Device = '01ef6900000001000000c5d3'
    Update_IMG = 'ccef690000'
    Stop_Video = '79ef6900000001'
    Display_Full_IMAGE = 'c8ef69001770'
    Query_Render_Status = 'cfef6900000001'
    Restart = '84ef6900000001'


class Unknown(enum.Enum):
    PreImgCMD = '2c'
    Media_Stop = '96ef6900000001'
    PostImgCMD = '86ef6900000001'
    OnExit = '87ef6900000001'


def OnExit(lcd_serial: serial.Serial):
    logger.info('Clean exit')
    SendMSG(lcd_serial, Unknown.OnExit)
    lcd_serial.close()


def ReadReply(lcd_serial: serial.Serial, expect=None, fail=True):
    logger.debug('ReadReply')
    # response = lcd_serial.read_until(b'\0').decode('utf-8').rstrip('\x00')

    response = None

    for i in range(5):
        response = lcd_serial.read(INPUT_MESSAGE_SIZE)
        logger.debug('Raw response: {}', binascii.hexlify(response))
        if response.startswith(binascii.unhexlify('5e41ef69')):
            logger.debug('Not initialized, retrying')
            continue
        response = response.decode('utf-8').rstrip('\x00')
        if response: break

    logger.debug('ReadReply: response {}', response if len(response) else '-- EMPTY --')
    if expect:
        logger.debug('Expect: {} type {} length {}', expect, type(expect), len(expect))
    # print(response)
    if expect:
        if str(response) != str(expect):
            if fail:
                raise Exception(f'Expected "{expect}" got "{response}"')
            else:
                logger.debug(f'Expected "{expect}" got "{response}"')

    return response


def SendMSG(lcd_serial: serial.Serial, MSG: Union[bytes, str, enum.Enum], PadValue='00'):
    if isinstance(MSG, enum.Enum):
        logger.debug('SendMSG: {}', MSG.name)
        MSG = MSG.value

    if type(MSG) is str: MSG = bytearray.fromhex(MSG)

    MsgSize = len(MSG)
    logger.debug('Message Size: {}', MsgSize)
    if not (MsgSize / MESSAGE_MAX_SIZE).is_integer():
        logger.debug('Padding Message')
        MSG += bytes.fromhex(PadValue) * ((MESSAGE_MAX_SIZE * ceil(MsgSize / MESSAGE_MAX_SIZE)) - MsgSize)

    lcd_serial.flushInput()
    logger.debug('SendMSG: first 64 bytes {} ', str(binascii.hexlify(bytes(MSG[:64]))))
    lcd_serial.write(MSG)
    return


def GenerateFullImage(Path):
    logger.debug('GenerateFullImage: {}', Path)
    image = cv2.imread(Path, cv2.IMREAD_UNCHANGED)
    logger.debug('Image shape: {}', image.shape)
    if image.shape[2] < 4: image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)

    image = bytearray(image)
    image = b'\x00'.join(image[i:i + 249] for i in range(0, len(image), 249))
    return image


def GenerateUpdateImage(Path, x, y):
    image = cv2.imread(Path, cv2.IMREAD_UNCHANGED)
    width = image.shape[0]
    height = image.shape[1]

    logger.debug('Generate update packet for image of size {},{} at {},{}', width,height, x,y)

    MSG = ''
    for h in range(height):
        MSG += f'{((x + h) * SCREEN_HEIGHT) + y:06x}' + f'{width:04x}'
        for w in range(width):
            MSG += f'{image[w][h][0]:02x}' + f'{image[w][h][1]:02x}' + f'{image[w][h][2]:02x}'

    UPD_Size = f'{int((len(MSG) / 2) + 2):04x}'  # The +2 is for the "ef69" that will be added later

    if len(MSG) > 500: MSG = '00'.join(MSG[i:i + 498] for i in range(0, len(MSG), 498))
    MSG += 'ef69'

    return MSG, UPD_Size


from serial.tools.list_ports import comports


def autodetect_known_devices():
    known_usb = [
        {"vid": "1D6B", "pid": "0106"}
    ]

    ret = []

    for port in comports():
        for known in known_usb:
            if port.pid and port.vid:
                if int.to_bytes(port.pid, length=2, byteorder='big') == bytes.fromhex(known['pid']) and int.to_bytes(port.vid, length=2, byteorder='big') == bytes.fromhex(
                        known['vid']):
                    ret.append(port.device)

    return ret


class FiveInchDriver:
    def __init__(self, com_port=None):
        self.com_port = com_port
        self.connected = False
        self.lcd_serial: Optional[serial.Serial] = None

    def _handle_autodetect(self):
        if self.com_port is None:
            known_devices = autodetect_known_devices()
            if len(known_devices) == 1 :
                logger.info('Found known device: {}', known_devices[0])
                self.com_port = known_devices[0]
            else:
                logger.error('Could not autodetect known device')

    def connect(self):
        self._handle_autodetect()
        if self.com_port:
            logger.info('connect to serial port {}', self.com_port)
            self.lcd_serial = serial.Serial(self.com_port, 115200, timeout=2, rtscts=1)

            SendMSG(self.lcd_serial, CMD.Get_Device)  # Skippable
            ReadReply(self.lcd_serial)
            self.connected = True
            SendMSG(self.lcd_serial, CMD.Stop_Video)  # Skippable if there is no video playing now
            ReadReply(self.lcd_serial)
            SendMSG(self.lcd_serial, Unknown.Media_Stop)  # Skippable, might be for album playback
            ReadReply(self.lcd_serial, 'media_stop')  # The reply should be "media_stop"

    def __del__(self):
        if self.connected:
            OnExit(self.lcd_serial)

    def show_image(self, png_path):
        SendMSG(self.lcd_serial, Unknown.PreImgCMD, '2c')  # Skippable, the app pads it using "2c" instead of 00
        SendMSG(self.lcd_serial, CMD.Display_Full_IMAGE)
        image = GenerateFullImage(png_path)
        SendMSG(self.lcd_serial, image)
        ReadReply(self.lcd_serial, "full_png_sucess")  # The reply should be "full_png_sucess"
        SendMSG(self.lcd_serial, Unknown.PostImgCMD)  # Skippable
        ReadReply(self.lcd_serial)
        SendMSG(self.lcd_serial, CMD.Query_Render_Status)
        ReadReply(
            self.lcd_serial)  # The reply should containts (needReSend:0) to confirm all message are read/deliverd in order

    def update_image(self, x,y, png_path):
        MSG, UPD_Size = GenerateUpdateImage(png_path, x, y)
        success_update = False
        while not success_update:
            SendMSG(self.lcd_serial, Unknown.PreImgCMD, '2c')  # Skippable, the app pads it using "2c" instead of 00
            SendMSG(self.lcd_serial, CMD.Update_IMG.value + UPD_Size)
            SendMSG(self.lcd_serial, MSG)

            ReadReply(self.lcd_serial)

            SendMSG(self.lcd_serial, Unknown.PostImgCMD)  # Skippable
            ReadReply(self.lcd_serial)

            SendMSG(self.lcd_serial,CMD.Query_Render_Status)
            reply = ReadReply(self.lcd_serial)
            if "needReSend:0" in reply:
                success_update=True
            else:
                logger.warning('need resend')


    def restart(self):
        SendMSG(self.lcd_serial, CMD.Restart)
        self.connected = False


def test_show_single_image():
    lcd = FiveInchDriver()
    lcd.connect()
    lcd.restart()
    time.sleep(12)
    lcd = FiveInchDriver()
    lcd.connect()
    lcd.show_image('./480.png')


def test_show_multiple_images():
    lcd = FiveInchDriver()
    lcd.connect()
    lcd.restart()
    time.sleep(12)
    lcd = FiveInchDriver()
    lcd.connect()
    lcd.show_image('./480.png')
    lcd.show_image('./800x480.png')
    lcd.show_image('./800x480_2.png')

def test_update_image():
    lcd = FiveInchDriver()
    lcd.connect()
    lcd.restart()
    time.sleep(12)
    lcd = FiveInchDriver()
    lcd.connect()
    lcd.show_image('./800x480.png')
    lcd.update_image(0, 0,"./100x30.png")
    #lcd.update_image(150, 0, "./100x30.png")
    #lcd.update_image(0, 150, "./100x30.png")
    #lcd.update_image(150, 150, "./100x30.png")
    #lcd.update_image(200, 200, "./100x30.png")



if __name__ == "__main__":
    #test_show_single_image()
    #test_show_multiple_images()
    test_update_image()
