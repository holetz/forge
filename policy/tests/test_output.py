import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from forge_policies import params  # noqa: E402
from forge_policies.output import cnpj_ok, cpf_ok, luhn_ok, output_guard  # noqa: E402

# Fake secrets are assembled at run time so no tracked file holds a literal.
KEY_HEADER = "-" * 5 + "BEGIN " + "RSA PRIV" + "ATE KEY" + "-" * 5
DAPI = "da" + "pi" + "0123456789abcdef" * 2
AWS = "AK" + "IA" + "ABCDEFGH12345678"
GH = "gh" + "p_" + "a1B2" * 9
JWT = ".".join(["ey" + "A" * 12, "ey" + "B" * 12, "C" * 12])


def cpf_make(nine):
    d = [int(c) for c in nine]
    for n in (9, 10):
        s = sum(v * w for v, w in zip(d, range(n + 1, 1, -1)))
        r = s % 11
        d.append(0 if r < 2 else 11 - r)
    return "".join(map(str, d))


def luhn_make(prefix):
    d = [int(c) for c in prefix]
    for check in range(10):
        if luhn_ok("".join(map(str, d + [check]))):
            return "".join(map(str, d + [check]))


def cnpj_make(twelve):
    d = [int(c) for c in twelve]
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for w in (w1, [6] + w1):
        r = sum(a * b for a, b in zip(d, w)) % 11
        d.append(0 if r < 2 else 11 - r)
    return "".join(map(str, d))


def resp(text, kind="response"):
    return {"type": kind, "data": text}


class OutputTest(unittest.TestCase):
    def setUp(self):
        self.ev = output_guard(**params.load_defaults()["output"])

    def test_secrets_denied_without_echo(self):
        for secret in (KEY_HEADER, DAPI, AWS, GH, JWT):
            out = self.ev(resp(f"here: {secret} done"))
            self.assertEqual(out["result"], "DENY", secret[:6])
            self.assertNotIn(secret, out["reason"])

    def test_validated_numbers(self):
        cpf = cpf_make("123456789")
        self.assertTrue(cpf_ok(cpf))
        out = self.ev(resp(f"cpf {cpf}"))
        self.assertEqual(out["result"], "DENY")
        self.assertNotIn(cpf, out["reason"])
        bad = cpf[:-1] + str((int(cpf[-1]) + 1) % 10)
        self.assertFalse(cpf_ok(bad))
        self.assertIsNone(self.ev(resp(f"cpf {bad}")))
        self.assertTrue(cnpj_ok(cnpj_make("112223330001")))
        self.assertEqual(self.ev(resp(cnpj_make("112223330001")))["result"], "DENY")
        card = luhn_make("411111111111111")
        self.assertEqual(self.ev(resp(f"card {card}"))["result"], "DENY")
        self.assertIsNone(self.ev(resp("order 4111111111111112")))

    def test_ssn(self):
        self.assertEqual(self.ev(resp("ssn 123-45-6789"))["result"], "DENY")
        self.assertIsNone(self.ev(resp("ssn 000-45-6789")))

    def test_tool_result_nested_and_phases(self):
        event = {"type": "tool_result", "data": {"result": {"rows": [{"k": AWS}]}}}
        self.assertEqual(self.ev(event)["result"], "DENY")
        self.assertIsNone(self.ev({"type": "tool_call", "data": {"arguments": {"c": AWS}}}))
        only = output_guard(detect=["cloud_key"], phases=["response"])
        self.assertIsNone(only(event))

    def test_email_phone_opt_in(self):
        self.assertIsNone(self.ev(resp("write to ana@example.com or 11 98765 4321")))
        opt = output_guard(detect=["email", "phone"])
        self.assertEqual(opt(resp("ana@example.com"))["result"], "DENY")
        self.assertEqual(opt(resp("call 11 98765 4321"))["result"], "DENY")

    def test_extra_patterns_and_ask(self):
        ev = output_guard(detect=[], extra_patterns=[r"proj-\d{4}"], action="ASK")
        out = ev(resp("see proj-1234"))
        self.assertEqual(out["result"], "ASK")
        self.assertNotIn("1234", out["reason"])

    def test_clean_text(self):
        self.assertIsNone(self.ev(resp("all good, 42 rows in 3 tables")))


if __name__ == "__main__":
    unittest.main()
