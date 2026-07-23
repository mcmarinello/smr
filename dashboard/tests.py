from django.template import Context, Template
from django.test import TestCase


class MaskAddressFilterTest(TestCase):
    def _render(self, address: str) -> str:
        template = Template("{% load dashboard_extras %}{{ address|mask_address }}")
        return template.render(Context({"address": address}))

    def test_masks_a_normal_address(self):
        result = self._render("0x" + "b" * 40)
        self.assertEqual(result, "0xbbbb••••bbbb")

    def test_short_string_is_returned_unchanged(self):
        result = self._render("0x1234")
        self.assertEqual(result, "0x1234")

    def test_empty_string_is_returned_unchanged(self):
        result = self._render("")
        self.assertEqual(result, "")
