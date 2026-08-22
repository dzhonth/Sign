import math
from typing import List, Dict

def calculate_dna(points: List[Dict]) -> Dict[str, int]:
    if not points or len(points) < 5:
        return {k: 5 for k in ['risk','speed','creativity','precision','empathy','autonomy','verbality']}
    length = 0
    for i in range(1, len(points)):
        dx = points[i]['x'] - points[i-1]['x']
        dy = points[i]['y'] - points[i-1]['y']
        length += math.hypot(dx, dy)
    duration = points[-1]['time'] - points[0]['time']
    if duration < 1:
        duration = 1000
    speed_raw = length / duration
    speed = min(10, max(1, int(speed_raw * 20)))
    xs = [p['x'] for p in points]
    ys = [p['y'] for p in points]
    span = (max(xs)-min(xs) + max(ys)-min(ys)) / 2
    risk = min(10, max(1, int(span / 25)))
    angles = 0
    for i in range(2, len(points)):
        x1,y1 = points[i-2]['x'], points[i-2]['y']
        x2,y2 = points[i-1]['x'], points[i-1]['y']
        x3,y3 = points[i]['x'], points[i]['y']
        a = (x2-x1)*(x3-x2) + (y2-y1)*(y3-y2)
        b = math.hypot(x2-x1, y2-y1) * math.hypot(x3-x2, y3-y2)
        if b != 0 and a/b < 0.3:
            angles += 1
    creativity = min(10, max(1, angles))
    mean_x = sum(xs)/len(xs)
    mean_y = sum(ys)/len(ys)
    variance = sum((x-mean_x)**2 + (y-mean_y)**2 for x,y in zip(xs,ys)) / len(xs)
    precision = min(10, max(1, 10 - int(variance / 500)))
    empathy = min(10, max(1, int(precision * 0.7 + 2)))
    verbality = min(10, max(1, 10 - int(speed / 1.5)))
    autonomy = min(10, max(1, int(span / 20 + length / 200)))
    return {
        'risk': risk,
        'speed': speed,
        'creativity': creativity,
        'precision': precision,
        'empathy': empathy,
        'autonomy': autonomy,
        'verbality': verbality
    }