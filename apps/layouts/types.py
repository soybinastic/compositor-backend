from enum import Enum


class LayoutType(str, Enum):
    CONTAIN = 'CONTAIN'
    COVER = 'COVER'
    THUMBNAIL = 'THUMBNAIL'
    GRID = 'GRID'
    SIDE_BY_SIDE = 'SIDE_BY_SIDE'
    HALFSCREEN = 'HALFSCREEN'
    SPOTLIGHT = 'SPOTLIGHT'
    CINEMA = 'CINEMA'
    PICTURE_IN_PICTURE = 'PICTURE_IN_PICTURE'
    OVERLAY = 'OVERLAY'
    FULLSCREEN = 'FULLSCREEN'


class ScaleMode(str, Enum):
    CONTAIN = 'contain'
    COVER = 'cover'
