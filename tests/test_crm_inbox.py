"""Tests of cli.crm_inbox (CRM Diffuseur -> Bluesky). No network: the publisher is injected.

Run: python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="crm-inbox-")
os.environ["CRM_INBOX_DIR"] = os.path.join(TMP, "crm_inbox")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli import crm_inbox as C  # noqa: E402

NOW = datetime(2026, 11, 3, 11, 45, tzinfo=timezone.utc)


def deposer(post_id, publier_a, **extra):
    C.INBOX.mkdir(parents=True, exist_ok=True)
    img = Path(TMP) / f"img-{post_id}.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"0" * 1000)
    item = {"version": 1, "id": post_id, "texte": "Une ruelle sous la pluie.\nhttps://jurojin.net/r/", "lien": "https://jurojin.net/r/",
            "image_path": str(img), "image_url": None, "alt": "Ruelle", "publier_a": publier_a, "tentatives": 0, **extra}
    (C.INBOX / f"crm-{post_id}.json").write_text(json.dumps(item), encoding="utf-8")
    return item


def resultat(post_id):
    return json.loads((C.OUTBOX / f"crm-{post_id}.json").read_text(encoding="utf-8"))


class CrmInboxTest(unittest.TestCase):
    def setUp(self):
        for d in (C.INBOX, C.OUTBOX):
            d.mkdir(parents=True, exist_ok=True)
            for f in d.iterdir():
                f.unlink()

    def test_publie_ce_qui_est_echu_un_par_passage_dans_l_ordre(self):
        deposer(2, "2026-11-03T11:30:00Z")
        deposer(1, "2026-11-03T11:00:00Z")
        deposer(3, "2026-11-04T11:30:00Z")  # demain : pas encore
        vus = []
        pub = lambda item: (vus.append(item["id"]) or {"success": True, "uri": f"at://x/{item['id']}"})
        r = C.run(now=NOW, publisher=pub)
        self.assertEqual(r, {"published": 1, "failed": 0, "pending": 1})
        self.assertEqual(vus, [1], "le plus ancien d'abord, un seul par passage")
        self.assertEqual(resultat(1)["statut"], "publie")
        self.assertEqual(resultat(1)["uri"], "at://x/1")
        self.assertFalse((C.INBOX / "crm-1.json").exists())
        C.run(now=NOW, publisher=pub)
        self.assertEqual(vus, [1, 2])
        self.assertEqual(C.run(now=NOW, publisher=pub), {"published": 0, "failed": 0, "pending": 0})
        self.assertTrue((C.INBOX / "crm-3.json").exists(), "la publication de demain attend")

    def test_trois_essais_puis_erreur_definitive(self):
        deposer(5, "2026-11-03T11:30:00Z")
        ko = lambda item: {"success": False, "error": "Failed to connect to Bluesky"}
        C.run(now=NOW, publisher=ko)
        item = json.loads((C.INBOX / "crm-5.json").read_text())
        self.assertEqual(item["tentatives"], 1)
        self.assertEqual(item["derniere_erreur"], "Failed to connect to Bluesky")
        self.assertFalse((C.OUTBOX / "crm-5.json").exists())
        C.run(now=NOW, publisher=ko)
        C.run(now=NOW, publisher=ko)
        self.assertEqual(resultat(5), {"id": 5, "statut": "erreur", "erreur": "Failed to connect to Bluesky"})
        self.assertFalse((C.INBOX / "crm-5.json").exists())

    def test_exception_du_publieur_comptee_comme_echec(self):
        deposer(6, "2026-11-03T11:30:00Z")
        def boom(item):
            raise RuntimeError("image introuvable")
        r = C.run(now=NOW, publisher=boom)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(json.loads((C.INBOX / "crm-6.json").read_text())["derniere_erreur"], "image introuvable")

    def test_plus_de_24_h_de_retard_abandonnee(self):
        deposer(7, (NOW - timedelta(hours=30)).isoformat().replace("+00:00", "Z"))
        r = C.run(now=NOW, publisher=lambda item: self.fail("ne doit pas publier"))
        self.assertEqual(r["failed"], 1)
        self.assertIn("replanifiez", resultat(7)["erreur"])

    def test_simulation_ne_touche_a_rien(self):
        deposer(8, "2026-11-03T11:30:00Z")
        r = C.run(now=NOW, dry_run=True, publisher=lambda item: self.fail("ne doit pas publier"))
        self.assertEqual(r["pending"], 1)
        self.assertTrue((C.INBOX / "crm-8.json").exists())

    def test_fichier_en_cours_abandonne_remis_en_file(self):
        deposer(9, "2026-11-03T11:30:00Z")
        f = C.INBOX / "crm-9.json"
        claimed = f.with_name(f.name + ".processing")
        os.replace(f, claimed)
        vieux = (NOW - timedelta(hours=3)).timestamp()
        os.utime(claimed, (vieux, vieux))
        C.run(now=NOW, publisher=lambda item: {"success": True, "uri": "at://x/9"})
        self.assertEqual(resultat(9)["statut"], "publie")

    def test_image_trop_lourde_refusee(self):
        img = Path(TMP) / "lourde.jpg"
        img.write_bytes(b"0" * (C.BLUESKY_IMAGE_MAX + 1))
        with self.assertRaises(RuntimeError):
            C.read_image({"image_path": str(img)})


if __name__ == "__main__":
    unittest.main()
