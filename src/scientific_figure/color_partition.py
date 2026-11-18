"""Deterministic perceptual color hypothesis for layered surfaces.

The palette is a drawing hypothesis, never a scientific category annotation.
Weighted Lab clustering avoids RGB median-cut averaging distinct hues together.
"""
import cv2
import numpy as np


def surface_palette(image, active, colors):
    from .analysis import hex_color
    if type(colors) is not int or not 1 <= colors <= 16:
        raise ValueError("surface palette colors must be in [1,16]")
    samples = image[active].astype(float)
    if not len(samples):
        raise ValueError("empty surface palette")
    bins = (samples.astype(np.uint8)//8)*8+4
    unique, inverse, counts = np.unique(bins, axis=0, return_inverse=True, return_counts=True)
    means = np.stack([np.bincount(inverse, weights=samples[:,i], minlength=len(counts))/counts for i in range(3)],axis=1)
    lab = cv2.cvtColor(np.rint(means)[None,:,:].astype(np.uint8), cv2.COLOR_RGB2LAB)[0].astype(float)
    count = min(colors,len(lab))
    centers = [lab[int(np.argmax(counts))]]
    for _ in range(1,count):
        distance = ((lab[:,None,:]-np.asarray(centers)[None,:,:])**2).sum(axis=2).min(axis=1)
        centers.append(lab[int(np.argmax(distance*np.sqrt(counts)))])
    centers = np.asarray(centers)
    previous = None
    for _ in range(24):
        labels = ((lab[:,None,:]-centers[None,:,:])**2).sum(axis=2).argmin(axis=1)
        if previous is not None and np.array_equal(labels,previous):
            break
        previous = labels.copy()
        for i in range(count):
            included = labels==i
            if included.any():
                centers[i] = np.average(lab[included],axis=0,weights=counts[included])
    palette = []
    for i in range(count):
        included=labels==i
        if included.any():
            color=hex_color(np.rint(np.average(means[included],axis=0,weights=counts[included])))
            if color not in palette:
                palette.append(color)
    return palette
