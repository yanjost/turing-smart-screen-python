import binascii
import time
from math import ceil
from typing import Union
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



#import logging

SCREEN_HEIGHT = 800

INPUT_MESSAGE_SIZE = 1024

MESSAGE_MAX_SIZE = 250

#logger = logging.getLogger()
#logging.basicConfig()
#logger.setLevel(logging.DEBUG)

FAIL_ON_EXPECT = True


class CMD(enum.Enum):
    Get_Device = '01ef6900000001000000c5d3'
    Update_IMG = 'ccef690000'
    Stop_Video = '79ef6900000001'
    Display_Full_IMAGE = 'c8ef69001770'
    Query_Render_Status = 'cfef6900000001'


class Unknown(enum.Enum):
    PreImgCMD = '2c'
    Media_Stop = '96ef6900000001'
    PostImgCMD = '86ef6900000001'
    OnExit = '87ef6900000001'


def OnExit():
    SendMSG(Unknown.OnExit)
    lcd_serial.close()


def ReadReply(expect=None, fail=True):
    logger.debug('ReadReply')
    # response = lcd_serial.read_until(b'\0').decode('utf-8').rstrip('\x00')

    response = None

    for i in range(5):
        response = lcd_serial.read(INPUT_MESSAGE_SIZE).decode('utf-8').rstrip('\x00')
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


def SendMSG(MSG: Union[str, enum.Enum], PadValue='00'):
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

    # I didn't notice any speed difference in splitting the messages, but their app had random splits in their messages ...
    # MsgLimit = 111000
    # MSG = [MSG[i:i + MsgLimit] for i in range(0, len(MSG), MsgLimit)]
    # for part in MSG:
    #     time.sleep(0.5)
    #     logger.debug('SendMSG: first 64 bytes {} ', str(binascii.hexlify(bytes(part[:64]))))
    #     lcd_serial.write(part)


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

    MSG = ''
    for h in range(height):
        MSG += f'{((x + h) * SCREEN_HEIGHT) + y:06x}' + f'{width:04x}'
        for w in range(width): MSG += f'{image[h][w][0]:02x}' + f'{image[h][w][1]:02x}' + f'{image[h][w][2]:02x}'

    UPD_Size = f'{int((len(MSG) / 2) + 2):04x}'  # The +2 is for the "ef69" that will be added later

    if len(MSG) > 500: MSG = '00'.join(MSG[i:i + 498] for i in range(0, len(MSG), 498))
    MSG += 'ef69'

    return MSG, UPD_Size


lcd_serial = serial.Serial("/dev/ttyACM0", 115200, timeout=1, rtscts=1)
atexit.register(OnExit)

SendMSG(CMD.Get_Device)  # Skippable
ReadReply()
SendMSG(CMD.Stop_Video)  # Skippable if there is no video playing now
ReadReply()
SendMSG(Unknown.Media_Stop)  # Skippable, might be for album playback
ReadReply('media_stop')  # The reply should be "media_stop"

import datetime

dt=datetime.datetime.now()

imgs = []

def generate_image_from_html(background_color=None):
    global imgs
    bgcolor=background_color or "red"
    logger.debug('start screenshot')
    imgs = hti.screenshot(
        html_str=str(dt),
        css_str=[f'body {{ background: {bgcolor} }}', 'body {font-size: 50px;}'],
        save_as='image.png'
    )
    logger.debug('end screenshot')


while True:
    generate_image_from_html(background_color='red')
    SendMSG(Unknown.PreImgCMD, '2c')  # Skippable, the app pads it using "2c" instead of 00
    SendMSG(CMD.Display_Full_IMAGE)
    # image = GenerateFullImage('./res/themes/LandscapeDeepSpace_theme_background.png')
    # image = GenerateFullImage('./800x480.png')
    image = GenerateFullImage(imgs[0])
    SendMSG(image)
    ReadReply("full_png_sucess")  # The reply should be "full_png_sucess"
    #SendMSG(Unknown.PostImgCMD)  # Skippable
    #ReadReply()

    SendMSG(CMD.Query_Render_Status)
    ReadReply()  # The reply should containts (needReSend:0) to confirm all message are read/deliverd in order

    time.sleep(3)
    #SendMSG(Unknown.PreImgCMD, '2c')  # Skippable, the app pads it using "2c" instead of 00
    SendMSG(CMD.Display_Full_IMAGE)
    # image = GenerateFullImage('./res/themes/LandscapeDeepSpace_theme_background.png')
    image = GenerateFullImage('./480.png')
    SendMSG(image)
    ReadReply("full_png_sucess", fail=False)  # The reply should be "full_png_sucess"
    #SendMSG(Unknown.PostImgCMD)  # Skippable
    #ReadReply()

    SendMSG(CMD.Query_Render_Status)
    ReadReply()  # The reply should containts (needReSend:0) to confirm all message are read/deliverd in order

    time.sleep(3)

# MSG, UPD_Size = GenerateUpdateImage('./res/themes/LandscapeTechnology_theme_background.png', 30, 50)
# SendMSG(CMD.Update_IMG + UPD_Size)
# SendMSG(MSG)
#
# SendMSG(CMD.Query_Render_Status)
# ReadReply()                                 #The reply should containts (needReSend:0) to confirm all message are read/deliverd in order
