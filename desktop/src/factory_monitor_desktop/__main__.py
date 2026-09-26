from __future__ import annotations

import argparse
import copy
import json
import multiprocessing
import os
from pathlib import Path
import sys


def default_detector_weights(config_path: Path, repository_root: Path) -> Path:
    """Select the source-checkout model location or installed-app data location."""
    config_path = Path(config_path)
    repository_root = Path(repository_root)
    implementation = repository_root / 'implementation'
    if (implementation / 'pyproject.toml').is_file():
        return (implementation / 'models' / 'yolo11n.pt').resolve()
    return (config_path.parent / 'models' / 'yolo11n.pt').resolve()


def resolve_detector_weights(
    config_path: Path,
    configured_path: str | Path,
    *,
    explicit_path: Path | None = None,
    working_directory: Path | None = None,
) -> Path:
    """Resolve configured paths relative to the config; CLI overrides use cwd."""
    candidate = Path(explicit_path if explicit_path is not None else configured_path)
    if candidate.is_absolute():
        return candidate.resolve()
    if explicit_path is not None:
        base = Path.cwd() if working_directory is None else Path(working_directory)
    else:
        base = Path(config_path).parent
    return (base / candidate).resolve()


def default_home() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'FactoryMonitorDesktop'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'FactoryMonitorDesktop'
    return Path.home() / '.local' / 'share' / 'factory-monitor-desktop'


def prepare_runtime_config(config, config_path, *, resource_profile, gateway_endpoint=None,
                           model=None, detector_weights=None, enable_review=False):
    from .resource_budget import apply_runtime_budget
    if resource_profile not in {'vram8gb', 'existing'}:
        raise ValueError('unknown resource profile')
    result = apply_runtime_budget(config) if resource_profile == 'vram8gb' else copy.deepcopy(config)
    result['detection']['model_path'] = str(resolve_detector_weights(
        config_path, result['detection']['model_path'], explicit_path=detector_weights))
    if enable_review:
        result['review']['enabled'] = True
    if result['review']['enabled']:
        if resource_profile == 'vram8gb' and not gateway_endpoint:
            raise ValueError('8GB 复核必须提供 --routes，经资源保护网关运行；请先预热并验证模型。')
        if gateway_endpoint:
            result['review']['endpoint'] = gateway_endpoint
            result['review']['model'] = model
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='本地桌面告警工作台；点击开始才采集。')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--source', choices=('demo', 'video', 'live'), default='demo')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--routes', type=Path, help='本地模型协议/备用服务配置')
    parser.add_argument('--resource-profile', choices=('vram8gb', 'existing'), default='vram8gb',
                        help='默认 8GB 保守资源保护；existing 显式沿用配置，不提供 8GB 保护')
    parser.add_argument('--detector-weights', type=Path,
                        help='覆盖本地 YOLO 检测权重路径；相对路径按当前工作目录解析')
    parser.add_argument('--enable-review', action='store_true', help='显式启用真实本地模型复核')
    parser.add_argument('--probe', action='store_true', help='只输出本机硬件建议，不启动界面或模型')
    args = parser.parse_args(argv)
    from .profiles import probe_hardware, recommend_profile
    if args.probe:
        hardware = probe_hardware()
        print(json.dumps({'hardware': hardware, 'recommendation': recommend_profile(hardware)},
                         ensure_ascii=False, indent=2))
        return 0
    from .qt_bootstrap import configure_qt_platform_plugin_path
    configure_qt_platform_plugin_path()
    from PySide6.QtWidgets import QApplication
    from factory_monitor.config import default_config, save_config
    from .app import DesktopWindow, ProtectedRuntime
    config_path = (args.config or default_home() / 'config.json').resolve()
    data_dir = (args.data_dir or default_home() / 'data').resolve()
    repository_root = Path(__file__).resolve().parents[3]
    if not config_path.exists():
        config = default_config()
        config['review']['enabled'] = False
        config['detection']['model_path'] = str(default_detector_weights(config_path, repository_root))
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config(config, config_path)
    gateway = None
    routes = None
    endpoint = None
    sampler = None
    try:
        policy = None
        if args.resource_profile == 'vram8gb':
            from .resource_budget import ResourceSampler, Vram8gbPolicy
            sampler = ResourceSampler(data_dir / 'logs' / 'resource-pressure.jsonl')
            sampler.start()
            policy = Vram8gbPolicy(sampler)
        if args.routes:
            from .models import ModelRouter
            from .gateway import Gateway
            routes = json.loads(args.routes.read_text(encoding='utf-8'))
            gateway = Gateway(ModelRouter(routes, resource_policy=policy))
            endpoint = gateway.start()

        def runtime_factory(config, target_dir):
            config = prepare_runtime_config(
                config, config_path, resource_profile=args.resource_profile,
                gateway_endpoint=endpoint, model=routes['providers'][0]['model'] if routes else None,
                detector_weights=args.detector_weights, enable_review=args.enable_review)
            return ProtectedRuntime(config, target_dir)

        app = QApplication.instance() or QApplication(sys.argv[:1])
        window = DesktopWindow(config_path, data_dir, runtime_factory=runtime_factory,
                               routing_summary=routes, resource_sampler=sampler)
        window.source_selector.setCurrentText(args.source)
        if args.input:
            window.input_path.setText(str(args.input.resolve()))
        window.show()
        return app.exec()
    finally:
        if gateway:
            gateway.close()
        if sampler:
            sampler.close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
