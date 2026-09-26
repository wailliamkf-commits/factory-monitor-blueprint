"""Static release-contract checks for source/resource tag separation."""

from pathlib import Path
import unittest

from scripts import verify_repository


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowContractTests(unittest.TestCase):
    def test_repository_gate_requires_new_guides_and_desktop_payload(self) -> None:
        self.assertIn('11-upgrade-and-device-strategy.md', verify_repository.REQUIRED_DOCS)
        self.assertIn('12-demonstration-and-field-run.md', verify_repository.REQUIRED_DOCS)
        self.assertIn('desktop/src/factory_monitor_desktop/app.py', verify_repository.REQUIRED_DESKTOP_FILES)
        self.assertIn('desktop/tests/test_gateway.py', verify_repository.REQUIRED_DESKTOP_FILES)
        self.assertIn('scripts/start-desktop.ps1', verify_repository.REQUIRED_DESKTOP_FILES)
        self.assertIn('local-model-routes.example.json', verify_repository.REQUIRED_DESKTOP_TEMPLATES)

    def test_release_workflow_uses_separate_validated_tag_inputs(self) -> None:
        workflow = (ROOT / '.github/workflows/verify-release-download.yml').read_text(encoding='utf-8')
        self.assertIn('blueprint_tag:', workflow)
        self.assertIn('default: v0.2.0-preview', workflow)
        self.assertIn('resources_tag:', workflow)
        self.assertIn('default: v0.1.0-preview', workflow)
        self.assertIn('BLUEPRINT_TAG: ${{ inputs.blueprint_tag }}', workflow)
        self.assertIn('RESOURCES_TAG: ${{ inputs.resources_tag }}', workflow)
        self.assertIn("pattern.fullmatch(value)", workflow)
        self.assertIn("['gh', 'release', 'download', release_tag", workflow)
        self.assertNotIn('GITHUB_SHA', workflow)
        self.assertIn("['git', 'rev-parse', f'{blueprint_tag}^{{commit}}']", workflow)
        self.assertIn("'scripts/verify_offline_zip.py'", workflow)
        self.assertIn('FactoryMonitor-Windows-20260925.zip.parts.json', workflow)


if __name__ == '__main__':
    unittest.main()
