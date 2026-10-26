"""Visualize two dry-run predictions on identical generated pixels, not a desktop."""
from pathlib import Path

from PIL import Image, ImageDraw

from .data import HEIGHT, WIDTH, goal_text, make_counterfactuals
from .model import Predictor


def preview(model, output, *, seed=12345, threshold=.45):
    output = Path(output)
    if output.exists():
        raise ValueError('预览文件已存在，请选择新文件名。')
    samples = make_counterfactuals(seed, language='en')
    predictor = Predictor(model, threshold=threshold)
    scale, header = 3, 62
    width = WIDTH * scale
    canvas = Image.new('RGB', (width * 2, HEIGHT * scale + header + 32), '#fcfcfb')
    draw = ImageDraw.Draw(canvas)
    results = []
    for index, sample in enumerate(samples):
        prediction = predictor.predict(sample.image, sample.goal)
        results.append({'goal': goal_text(sample.goal_tokens), **prediction.as_dict()})
        x_offset = width * index
        draw.text((x_offset + 10, 8), sample.goal, fill='#233142')
        draw.text((x_offset + 10, 27),
                  f'{prediction.proposed_action} | confidence {prediction.confidence:.3f}', fill='#233142')
        draw.text((x_offset + 10, 43), 'Same image, different goal; prediction only', fill='#526474')
        canvas.paste(sample.image.resize((width, HEIGHT * scale), Image.Resampling.NEAREST), (x_offset, header))
        if prediction.normalized_xy is not None:
            x = x_offset + prediction.normalized_xy[0] * (WIDTH - 1) * scale
            y = header + prediction.normalized_xy[1] * (HEIGHT - 1) * scale
            draw.ellipse((x - 11, y - 11, x + 11, y + 11), outline='#ffffff', width=5)
            draw.line((x - 12, y, x + 12, y), fill='#101010', width=2)
            draw.line((x, y - 12, x, y + 12), fill='#101010', width=2)
    draw.text((10, HEIGHT * scale + header + 10),
              'Synthetic-only research model. No actual mouse movement or clicks.', fill='#526474')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        canvas.save(stream, format='PNG')
    return {'domain': 'synthetic-only', 'execution_permitted': False, 'predictions': results}
