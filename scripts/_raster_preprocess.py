"""Non-generative viewing aids. No semantic masks or coordinate changes."""
from PIL import Image, ImageOps

MODES = ('none', 'gray', 'autocontrast', 'neutral-ink', 'palette-denoise', 'local-contrast')


def enhance(image, mode='none', kernel=21):
    if mode not in MODES:
        raise ValueError('Unknown preprocessing mode')
    if type(kernel) is not int or kernel < 3 or kernel > 101 or kernel % 2 == 0:
        raise ValueError('Background kernel must be odd, in [3,101] original pixels')
    if mode == 'none':
        return image.copy()
    if mode == 'gray':
        return ImageOps.grayscale(image)
    if mode == 'autocontrast':
        return ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=0)
    # Optional dependencies loaded only for the corresponding viewing aid.
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise ValueError("This viewing aid requires optional numpy and opencv-python; use none/gray/autocontrast otherwise") from exc
    rgb = np.asarray(image.convert('RGB'))
    if mode == 'neutral-ink':
        # Adapted from the user's local optical-density experiment. Closing only
        # estimates background; it is never used as a repaired map or line mask.
        element = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel,kernel))
        background = cv2.morphologyEx(rgb, cv2.MORPH_CLOSE, element).astype(np.float32)
        density = np.maximum(np.log((background+8)/(rgb.astype(np.float32)+8)), 0)
        maximum = density.max(axis=2)
        neutrality = np.clip(density.min(axis=2)/(maximum+.025),0,1)
        strength = np.clip((maximum*neutrality**2.2-.020)/(.50-.020),0,1)**.75
        return Image.fromarray(np.rint(255*(1-strength)).astype(np.uint8))
    lab = cv2.cvtColor(rgb,cv2.COLOR_RGB2LAB)
    light,a,b = cv2.split(lab)
    if mode == 'local-contrast':
        # Viewing-only luminance contrast: retain chroma, avoid binary masks.
        enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(light)
        light = cv2.addWeighted(light,.5,enhanced,.5,0)
        return Image.fromarray(cv2.cvtColor(cv2.merge((light,a,b)),cv2.COLOR_LAB2RGB))
    light = cv2.bilateralFilter(light,5,6,2,borderType=cv2.BORDER_REPLICATE)
    def chroma(channel):
        return cv2.bilateralFilter(cv2.medianBlur(channel,3),5,9,2,borderType=cv2.BORDER_REPLICATE)
    return Image.fromarray(cv2.cvtColor(cv2.merge((light,chroma(a),chroma(b))),cv2.COLOR_LAB2RGB))


def crop_aid(image, bounds, mode, kernel=21):
    """Neighborhood filters use a source-pixel halo before cropping back."""
    left,top,right,bottom=bounds
    halo=kernel-1 if mode=='neutral-ink' else (5 if mode=='palette-denoise' else 0)
    extended=(max(0,left-halo),max(0,top-halo),min(image.width,right+halo),min(image.height,bottom+halo))
    result=enhance(image.crop(extended).convert('RGB'),mode,kernel)
    return result.crop((left-extended[0],top-extended[1],right-extended[0],bottom-extended[1]))
