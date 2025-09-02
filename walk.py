#!/usr/bin/env python3
# sh1106_walk_on_bbb_scroll.py
# Random walk renderer for SH1106 128x64 on BBB I2C bus 1 addr 0x3C
# Walker (2x2) stays fixed at display center; world scrolls smoothly beneath it.
# Requires: pip3 install smbus2 pillow
# This is Duck AI* Generated. not my work!!

from smbus2 import SMBus
from PIL import Image, ImageDraw, ImageFont
import time
import random
import math
import sys

# --- Display constants ---
I2C_BUS = 1
I2C_ADDR = 0x3C
VISIBLE_WIDTH = 128
HEIGHT = 64
PAGES = HEIGHT // 8
CMD = 0x00
DATA = 0x40

INIT_SEQ = [
    (CMD, 0xAE), (CMD, 0xD5), (CMD, 0x80), (CMD, 0xA8), (CMD, 0x3F),
    (CMD, 0xD3), (CMD, 0x00), (CMD, 0x40), (CMD, 0xAD), (CMD, 0x8B),
    (CMD, 0xA1), (CMD, 0xC8), (CMD, 0xDA), (CMD, 0x12), (CMD, 0x81),
    (CMD, 0x7F), (CMD, 0xD9), (CMD, 0x22), (CMD, 0xDB), (CMD, 0x40),
    (CMD, 0xA4), (CMD, 0xA6), (CMD, 0xAF),
]

class SH1106:
    def __init__(self, bus=I2C_BUS, addr=I2C_ADDR):
        self.bus_num = bus
        self.addr = addr
        self.bus = SMBus(self.bus_num)
        for btype, val in INIT_SEQ:
            self._write_cmd(val)
        self.buffer = bytearray(VISIBLE_WIDTH * PAGES)

    def _write_cmd(self, byte):
        self.bus.write_byte_data(self.addr, CMD, byte)

    def _write_data(self, data):
        chunk_size = 32
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i+chunk_size]
            self.bus.write_i2c_block_data(self.addr, DATA, list(chunk))

    def show(self):
        col_offset = 2
        for page in range(PAGES):
            self._write_cmd(0xB0 + page)
            self._write_cmd(0x00 + (col_offset & 0x0F))
            self._write_cmd(0x10 + ((col_offset >> 4) & 0x0F))
            start = page * VISIBLE_WIDTH
            end = start + VISIBLE_WIDTH
            page_bytes = self.buffer[start:end]
            self._write_data(page_bytes)

    def clear(self):
        for i in range(len(self.buffer)):
            self.buffer[i] = 0x00

    def pixel(self, x, y, color=1):
        if not (0 <= x < VISIBLE_WIDTH and 0 <= y < HEIGHT):
            return
        page = y // 8
        idx = x + page * VISIBLE_WIDTH
        bit = 1 << (y & 7)
        if color:
            self.buffer[idx] |= bit
        else:
            self.buffer[idx] &= ~bit

    def image(self, pil_img):
        if pil_img.mode != '1':
            pil_img = pil_img.convert('1')
        img = pil_img.crop((0, 0, VISIBLE_WIDTH, HEIGHT)).convert('1')
        pix = img.load()
        for y in range(HEIGHT):
            for x in range(VISIBLE_WIDTH):
                if pix[x, y] == 255:
                    self.pixel(x, y, 1)
                else:
                    self.pixel(x, y, 0)

    def close(self):
        try:
            self.bus.close()
        except:
            pass

# --- Walk simulator config ---
PIXELS_PER_METER = 1.5
MIN_DISTANCE_M = 1.0
MAX_DISTANCE_M = 18.0
MIN_PAUSE_S = 0.2
MAX_PAUSE_S = 3.0
WALK_SPEED_MPS = 1.4

DIRECTIONS = {
    "N":  (0, -1),
    "NE": (1, -1),
    "E":  (1, 0),
    "SE": (1, 1),
    "S":  (0, 1),
    "SW": (-1, 1),
    "W":  (-1, 0),
    "NW": (-1, -1),
}

class WalkSim:
    def __init__(self, oled, cycles=50, world_margin=256):
        """
        world_margin: extra pixels beyond the visible window in all directions to avoid reallocation.
        """
        self.oled = oled
        self.cycles_target = cycles
        self.cycles_done = 0

        # screen center where walker will be fixed
        self.cx = VISIBLE_WIDTH // 2
        self.cy = HEIGHT // 2

        # world coordinate of the walker (float for smooth)
        self.world_x = 0.0
        self.world_y = 0.0

        # world image size: make larger than visible window to allow scrolling around
        self.world_w = VISIBLE_WIDTH + world_margin * 2
        self.world_h = HEIGHT + world_margin * 2

        # origin offset: map world (0,0) to pixel (ox + world_margin, oy + world_margin)
        # We'll center the initial world walker in the world image center.
        self.world_origin_x = self.world_w // 2
        self.world_origin_y = self.world_h // 2
        self.world_x = 0.0
        self.world_y = 0.0

        # create offscreen world image to draw paths into
        self.world_img = Image.new('1', (self.world_w, self.world_h), color=0)
        self.world_draw = ImageDraw.Draw(self.world_img)

        # walker visual size (2x2)
        self.rect_half = 1

        # draw initial walker position marker into world (so path starts visible)
        self._draw_walker_in_world(self.world_x, self.world_y)

        self.energy = 1.0

        # buffer image (what will be blitted to display each frame)
        self.screen_img = Image.new('1', (VISIBLE_WIDTH, HEIGHT), color=0)

        # push initial frame
        self._blit_world_to_screen_and_show()

    def world_to_image_coords(self, wx, wy):
        """
        Convert world coords (wx, wy) to pixel coords in the world image.
        world origin (0,0) maps to (world_origin_x, world_origin_y).
        """
        ix = int(round(self.world_origin_x + wx))
        iy = int(round(self.world_origin_y + wy))
        return ix, iy

    def _draw_walker_in_world(self, wx, wy):
        ix, iy = self.world_to_image_coords(wx, wy)
        h = self.rect_half
        x0 = max(0, ix - h)
        y0 = max(0, iy - h)
        x1 = min(self.world_w - 1, ix + h)
        y1 = min(self.world_h - 1, iy + h)
        self.world_draw.rectangle([x0, y0, x1, y1], fill=255)

    def _draw_line_in_world(self, wx0, wy0, wx1, wy1):
        ix0, iy0 = self.world_to_image_coords(wx0, wy0)
        ix1, iy1 = self.world_to_image_coords(wx1, wy1)
        self.world_draw.line([ix0, iy0, ix1, iy1], fill=255)

    def _blit_world_to_screen_and_show(self):
        """
        Copy the relevant window from the world image into the screen image so that
        the world point (world_x, world_y) appears at screen center (cx, cy).
        """
        # compute top-left of the window within world image
        center_ix, center_iy = self.world_to_image_coords(self.world_x, self.world_y)
        left = center_ix - self.cx
        top = center_iy - self.cy

        # clamp window to world image bounds (pad with black if necessary)
        if left < 0:
            left = 0
        if top < 0:
            top = 0
        if left + VISIBLE_WIDTH > self.world_w:
            left = self.world_w - VISIBLE_WIDTH
        if top + HEIGHT > self.world_h:
            top = self.world_h - HEIGHT

        window = self.world_img.crop((left, top, left + VISIBLE_WIDTH, top + HEIGHT))
        self.screen_img.paste(window, (0, 0))

        # draw fixed walker on screen center (so it stays visually centered)
        draw_screen = ImageDraw.Draw(self.screen_img)
        h = self.rect_half
        sx0 = max(0, self.cx - h)
        sy0 = max(0, self.cy - h)
        sx1 = min(VISIBLE_WIDTH - 1, self.cx + h)
        sy1 = min(HEIGHT - 1, self.cy + h)
        draw_screen.rectangle([sx0, sy0, sx1, sy1], fill=255)

        # send to OLED
        self.oled.clear()
        self.oled.image(self.screen_img)
        self.oled.show()

    def step(self):
        # pick random direction and distance as before
        dir_name = random.choice(list(DIRECTIONS.keys()))
        dx_norm, dy_norm = DIRECTIONS[dir_name]
        norm_factor = 1.0
        if dx_norm != 0 and dy_norm != 0:
            norm_factor = 1 / math.sqrt(2)

        distance_m = random.uniform(MIN_DISTANCE_M, MAX_DISTANCE_M)
        delta_x = dx_norm * distance_m * PIXELS_PER_METER * norm_factor
        delta_y = dy_norm * distance_m * PIXELS_PER_METER * norm_factor

        new_world_x = self.world_x + delta_x
        new_world_y = self.world_y + delta_y

        # draw path in world coordinates
        self._draw_line_in_world(self.world_x, self.world_y, new_world_x, new_world_y)
        # update world position
        self.world_x, self.world_y = new_world_x, new_world_y

        # draw walker marker into world image at new position too (so trails show if later view changes)
        self._draw_walker_in_world(self.world_x, self.world_y)

        # blit window centered on world_x/world_y so walker appears fixed at screen center
        self._blit_world_to_screen_and_show()

        # timing / energy (same as original)
        travel_time_s = distance_m / WALK_SPEED_MPS
        energy_cost = distance_m / 100.0
        self.energy = max(0.0, self.energy - energy_cost)
        extra_pause = 0.0
        if self.energy < 0.2:
            extra_pause = random.uniform(0.5, 1.5)
            self.energy = min(1.0, self.energy + 0.5)
        pause_s = min(MAX_PAUSE_S, max(MIN_PAUSE_S, travel_time_s * random.uniform(0.5, 1.5))) + extra_pause

        self.cycles_done += 1
        return pause_s

def show_prompt(oled, lines, wait=0.05):
    img = Image.new('1', (VISIBLE_WIDTH, HEIGHT), color=0)
    draw = ImageDraw.Draw(img)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except:
        font = None
    y = 0
    for line in lines:
        draw.text((0, y), line, font=font, fill=255)
        y += 12
    oled.clear()
    oled.image(img)
    oled.show()
    time.sleep(wait)

def ask_cycles_with_screen(oled):
    show_prompt(oled, ["Random Walk Simulator", "", "How many cycles?"])
    try:
        val = int(input("How many cycles to run? (e.g., 50) > "))
        if val < 1:
            return 1
        return val
    except Exception:
        return 50

def ask_start_confirm(oled):
    show_prompt(oled, ["Press Enter to START", "", "(Ctrl-C to cancel)"])
    try:
        input("")  # wait for Enter
        return True
    except KeyboardInterrupt:
        return False

def main():
    oled = SH1106()
    try:
        cycles = ask_cycles_with_screen(oled)
        if not ask_start_confirm(oled):
            print("Canceled")
            return
        sim = WalkSim(oled, cycles=cycles, world_margin=256)
        while sim.cycles_done < sim.cycles_target:
            pause = sim.step()
            time.sleep(pause)
    except KeyboardInterrupt:
        pass
    finally:
        oled.clear()
        oled.show()
        oled.close()

if __name__ == '__main__':
    main()
