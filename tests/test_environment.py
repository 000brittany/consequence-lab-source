import unittest


class EnvironmentTests(unittest.TestCase):
    def test_installed_package_imports(self):
        import consequence_lab

        self.assertEqual(consequence_lab.__version__, "0.1.0")
