"""Acces a Supabase : verification du compte et portefeuille.

Toutes les requetes partent avec le jeton de l'utilisateur connecte, jamais
avec une cle d'administration. La Row Level Security (schema.sql) garantit
donc qu'un compte ne lit et n'ecrit que ses propres lignes.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

import config

REST = config.SUPABASE_URL + "/rest/v1"


class NonAutorise(Exception):
    pass


class ErreurBase(Exception):
    pass


@dataclass
class Utilisateur:
    id: str
    email: str
    jeton: str


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entetes(jeton: str) -> dict:
    return {
        "apikey": config.SUPABASE_PUBLISHABLE_KEY,
        "Authorization": "Bearer " + jeton,
    }


def _verifier(r: httpx.Response) -> httpx.Response:
    if r.status_code in (401, 403):
        raise NonAutorise("Session expiree, reconnecte-toi.")
    if r.status_code >= 400:
        raise ErreurBase("Supabase a repondu %s : %s" % (r.status_code, r.text[:300]))
    return r


async def utilisateur(client: httpx.AsyncClient, jeton: str) -> Utilisateur:
    """Demande a Supabase a qui appartient ce jeton. Refuse s'il est invalide."""
    r = await client.get(config.SUPABASE_URL + "/auth/v1/user", headers=_entetes(jeton))
    if r.status_code != 200:
        raise NonAutorise("Session invalide ou expiree, reconnecte-toi.")
    donnees = r.json()
    return Utilisateur(id=donnees["id"], email=donnees.get("email", ""), jeton=jeton)


async def lire_positions(client: httpx.AsyncClient, u: Utilisateur) -> list[dict]:
    r = await client.get(
        REST + "/positions",
        params={"select": "id,ticker,quantite,prix_achat", "order": "ticker.asc"},
        headers=_entetes(u.jeton),
    )
    return _verifier(r).json()


async def enregistrer_position(client: httpx.AsyncClient, u: Utilisateur,
                               ticker: str, quantite: float, prix_achat: float) -> None:
    """Cree la ligne, ou remplace quantite et prix de revient si le titre y est deja."""
    r = await client.post(
        REST + "/positions",
        params={"on_conflict": "user_id,ticker"},
        json={"user_id": u.id, "ticker": ticker, "quantite": quantite,
              "prix_achat": prix_achat, "updated_at": _maintenant()},
        headers={**_entetes(u.jeton), "Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    _verifier(r)


async def supprimer_position(client: httpx.AsyncClient, u: Utilisateur, position_id: int) -> None:
    r = await client.delete(
        REST + "/positions",
        params={"id": "eq.%d" % position_id},
        headers=_entetes(u.jeton),
    )
    _verifier(r)


async def lire_liquidites(client: httpx.AsyncClient, u: Utilisateur) -> float:
    r = await client.get(
        REST + "/liquidites",
        params={"select": "montant"},
        headers=_entetes(u.jeton),
    )
    lignes = _verifier(r).json()
    return float(lignes[0]["montant"]) if lignes else 0.0


async def enregistrer_liquidites(client: httpx.AsyncClient, u: Utilisateur, montant: float) -> None:
    r = await client.post(
        REST + "/liquidites",
        params={"on_conflict": "user_id"},
        json={"user_id": u.id, "montant": montant, "updated_at": _maintenant()},
        headers={**_entetes(u.jeton), "Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    _verifier(r)
