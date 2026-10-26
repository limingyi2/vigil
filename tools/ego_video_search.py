"""Independent video content-index CLI. No Vigil tool registration or embeddings.

python tools/ego_video_search.py --ego-root F:/EgoAgent-Eval --video clip.mp4
    --question "钥匙放在哪里？" --allow-model-calls
Without --allow-model-calls only configuration is printed; no images are sent.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ego-root', type=Path, required=True)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--question', required=True)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--allow-model-calls', action='store_true')
    parser.add_argument('--max-calls', type=int, default=40)
    args = parser.parse_args()
    import model_hub
    import paths
    from video_analysis import VisionModel, VideoContentIndex, settings
    config = model_hub.load()
    if not args.allow_model_calls:
        print(json.dumps(dict(status='authorization_required', settings=settings(config)), ensure_ascii=False))
        return 2
    root = args.ego_root.resolve()
    if not (root / 'egoagent_eval/video.py').is_file():
        raise ValueError('ego-root 不是有效的 EgoAgent 源码目录')
    sys.path.insert(0, str(root))
    from egoagent_eval.video import Video
    model = VisionModel(config, authorized=True, max_calls=args.max_calls)
    with Video(args.video) as video:
        index = VideoContentIndex(video, model, args.cache_dir or Path(paths.DATA_DIR) / 'video_analysis/index')
        index.build(progress=lambda event: print(json.dumps(dict(type='index_progress', **event), ensure_ascii=False), file=sys.stderr))
        print(json.dumps(index.ask(args.question), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps(dict(status='failed', error=str(exc)), ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
