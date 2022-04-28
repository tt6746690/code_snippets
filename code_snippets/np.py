import numpy as np

def np_trim_upper(x, α=.005):
    """ Trim extreme values & normalize to [0,1] 
    Useful for processing dicom/xray images with extreme bright pixels """
    x_max = np.quantile(x, 1-α)
    x = np.clip(x, 0, x_max)
    x = x / max(1e-3, x.max())
    return x