import numpy as np
import pytest

from skyshift_core.candidates import false_alarm, cold_candidates
from skyshift_core.legacy.detect import psf_kernel
from skyshift_core.flags import MASK_BITS


def test_false_alarm_ties_and_finite_background_resolution():
    assert false_alarm(10, [0, 3, 10]) == .5
    assert false_alarm(11, [0, 3, 10]) == .25
    assert false_alarm(1, []) is None
    assert false_alarm(1, [np.nan]) is None


def cube(times):
    image=np.zeros((len(times)*2,21,21)); var=np.ones_like(image)
    meta=[]; detections=[]
    for k,t in enumerate(times):
        meta.extend([dict(mjd=t,det=3,obs=str(k),expo=1,ver='dr1'),
                     dict(mjd=t,det=6,obs=str(k),expo=1,ver='dr1')])
        image[2*k+1,7:14,7:14]=psf_kernel()*200
        detections.append(dict(t=2*k+1,x=10.,y=10.,snr=20.))
    return detections, meta, image, var, np.full(len(image),1.8)


def test_cold_source_requires_two_native_visits_and_keeps_missing_flags_unvetted():
    assert cold_candidates(*cube([60000,60020]))[0]['status']=='single_visit'
    result=cold_candidates(*cube([60000,60090]))[0]
    assert result['n_visits']==2
    assert result['status']=='unvetted'
    assert result['p_false'] is None
    args=cube([60000,60090]); flags=np.zeros_like(args[2],dtype=np.int32)
    assert cold_candidates(*args,flags=flags)[0]['status']=='persistent'
    flags[:,10,10]=MASK_BITS
    assert cold_candidates(*args,flags=flags)==[]


def test_missing_short_band_is_not_a_nondetection_and_red_color_is_forced_on_raw():
    args=cube([60000,60090]); args[2][::2]=np.nan
    assert cold_candidates(*args)==[]
    args=cube([60000,60090]); args[2][::2]=args[2][1::2]
    assert cold_candidates(*args)==[]
    args=cube([60000,60090]); args[1][0]['mjd']+=.01
    assert cold_candidates(*args)[0]['n_visits']==1
