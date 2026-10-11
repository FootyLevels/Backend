"""Shared scene alignment with reviewed contact constraints, not motion synthesis."""
import numpy as np


def reviewed_ranges(ranges, count):
    result = np.zeros(count, bool)
    for start, end in ranges:
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end < count):
            raise ValueError('Invalid inclusive contact range')
        result[start:end + 1] = True
    return result


def normal_to_up(normal):
    """Proper rotation from a camera-space ground normal to world +Y."""
    normal = np.asarray(normal, float)
    normal /= np.linalg.norm(normal)
    up = np.array([0., 1., 0.])
    cross = np.cross(normal, up)
    cosine = normal @ up
    if cosine < .2:
        raise ValueError('Ground normal is not plausibly upward')
    skew = np.array([[0, -cross[2], cross[1]], [cross[2], 0, -cross[0]], [-cross[1], cross[0], 0]])
    return np.eye(3) + skew + skew @ skew / (1 + cosine)


def ground_ray(position, normal, camera_height, contact_height):
    """Move along the observed image ray, preserving the detected pixel center."""
    denominator = np.dot(normal, position)
    if denominator >= -1e-6 or camera_height <= contact_height:
        raise ValueError('Image ray does not intersect a plausible ground-contact plane')
    return position * ((contact_height - camera_height) / denominator)


def rolling_median(values, radius=5):
    return np.array([np.median(values[max(0, i-radius):min(len(values), i+radius+1)], axis=0)
                     for i in range(len(values))])


def align_scene(vertices, translations, balls, valid, ball_valid, shots, observations, review, support_height):
    """Level camera pitch/roll, share scale, and apply only explicit contact labels.

    Body scale is fixed per shot. Depth translations correct contact without
    changing joint angles. Positive unconstrained flight height is retained;
    below-ground estimates receive a nonpenetration correction.
    """
    count = len(vertices)
    if review['frameCount'] != count:
        raise ValueError('Contact review belongs to a different frame count')
    grounded = reviewed_ranges(review['ballGroundRanges'], count) & ball_valid
    body_support = reviewed_ranges(review['bodySupportRanges'], count) & valid
    result = vertices.copy()
    corrected_ball = balls.copy()
    details, frames = [], [None] * count
    for shot in np.unique(shots):
        ids = np.flatnonzero(shots == shot)
        samples = [s for s in observations['samples'] if s['shot'] == shot and s['inlierFraction'] >= .65]
        anchors = ids[grounded[ids]]
        if len(samples) < 2 or len(anchors) < 8:
            raise ValueError(f'Shot {shot} needs reliable turf observations and at least eight reviewed ball contacts')
        samples.sort(key=lambda s: s['index'])
        sample_normals = rolling_median(np.asarray([s['normal'] for s in samples]), radius=1)
        normals = np.stack([np.interp(ids, [s['index'] for s in samples], sample_normals[:, j]) for j in range(3)], axis=1)
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        lookup = {int(i): k for k, i in enumerate(ids)}
        radius = review.get('diameterMetres', .22) / 2
        heights_raw = np.array([radius - normals[lookup[i]] @ balls[i] for i in anchors])
        heights_filtered = rolling_median(heights_raw, radius=5)
        if np.any((heights_filtered < .5) | (heights_filtered > 3)):
            raise ValueError('Ground calibration gave an implausible camera height')
        heights = np.interp(ids, anchors, heights_filtered)
        contact_ids = ids[body_support[ids]]
        if len(contact_ids) < 8:
            raise ValueError(f'Shot {shot} needs reviewed supporting-foot frames')
        scale = float(np.median([heights[lookup[i]] / -(vertices[i] @ normals[lookup[i]]).min() for i in contact_ids]))
        if not .65 <= scale <= 1.4:
            raise ValueError('Body/ball scale disagreement is too large for contact alignment')
        # Only a fixed horizontal origin is removed, retaining movement within a shot.
        first = int(ids[valid[ids]][0])
        first_rotation = normal_to_up(normals[lookup[first]])
        origin = first_rotation @ (translations[first] * scale)
        origin[1] = 0
        for k, i in enumerate(ids):
            normal, height = normals[k], heights[k]
            rotation = normal_to_up(normal)
            delta = np.zeros(3)
            if valid[i]:
                v = vertices[i] * scale
                clearance = float((v @ normal).min() + height)
                if body_support[i] or clearance < 0:
                    # Root translation stays along the root's camera ray.
                    root = translations[i] * scale
                    denominator = float(normal @ root)
                    if denominator >= -1e-6:
                        raise ValueError('Body camera translation cannot support ground alignment')
                    delta = -clearance / denominator * root
                    v += delta
                result[i] = v @ rotation.T + [0, height, 0] - origin
            constraint = 'missing'
            if ball_valid[i]:
                p = balls[i].copy()
                floor_height = float(normal @ p + height)
                constraint = 'reviewed-ground' if grounded[i] else 'unconstrained'
                if grounded[i] or floor_height < support_height:
                    p = ground_ray(p, normal, height, support_height)
                    if not grounded[i]:
                        constraint = 'nonpenetration'
                corrected_ball[i] = rotation @ p + [0, height, 0] - origin
            frames[i] = {'normal': normal.tolist(), 'cameraHeight': float(height),
                         'bodyDepthCorrection': float(np.linalg.norm(delta)),
                         'bodySupport': bool(body_support[i]), 'ballConstraint': constraint}
        details.append({'id': int(shot), 'start': int(ids[0]), 'origin': origin.tolist(),
            'bodyScale': scale, 'cameraHeightRange': [float(heights.min()), float(heights.max())],
            'reviewedBallFrames': len(anchors), 'reviewedBodyFrames': len(contact_ids)})
    return result, corrected_ball, details, frames, grounded, body_support


def enforce_contacts(vertices, balls, valid, ball_valid, grounded, body_support, support_height):
    """Maintain exact contact after smoothing; no new height is added to a flight."""
    for i in range(len(vertices)):
        if valid[i]:
            bottom = float(vertices[i, :, 1].min())
            if body_support[i] or bottom < 0:
                vertices[i, :, 1] -= bottom
        if ball_valid[i] and (grounded[i] or balls[i, 1] < support_height):
            balls[i, 1] = support_height
    return vertices, balls
