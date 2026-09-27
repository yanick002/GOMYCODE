"""Valorisation du portefeuille, en Python et non par le modele.

Le modele recoit ces chiffres deja calcules et les interprete. Un modele qui
additionne ou fait des pourcentages se trompe de temps en temps, sans le dire.
"""


def _nombre(valeur) -> float | None:
    if valeur is None:
        return None
    texte = str(valeur).replace("%", "").replace(",", ".").replace("+", "").strip()
    if not texte:
        return None
    try:
        return float(texte)
    except ValueError:
        return None


def valoriser(positions: list[dict], liquidites: float, indicateurs: dict[str, dict]) -> dict:
    """positions : lignes Supabase. indicateurs : {ticker: ligne get_indicators}.

    Un titre sans cours (ticker inconnu, MCP en panne) garde sa ligne avec des
    valeurs a None : il n'est jamais compte pour zero dans le total.
    """
    lignes = []
    for p in positions:
        quantite = float(p["quantite"])
        prix_achat = float(p["prix_achat"])
        ind = indicateurs.get(p["ticker"]) or {}
        cours = _nombre(ind.get("Cours_Actuel"))
        cout = quantite * prix_achat
        valeur = quantite * cours if cours is not None else None
        lignes.append({
            "id": p.get("id"),
            "ticker": p["ticker"],
            "quantite": quantite,
            "prix_achat": prix_achat,
            "cours": cours,
            "variation_jour": ind.get("Variation_Cours"),
            "rsi": _nombre(ind.get("RSI")),
            "cout": round(cout, 2),
            "valeur": round(valeur, 2) if valeur is not None else None,
            "plus_value": round(valeur - cout, 2) if valeur is not None else None,
            "plus_value_pct": round((valeur - cout) / cout * 100, 2) if valeur is not None and cout else None,
        })

    valorisees = [l for l in lignes if l["valeur"] is not None]
    valeur_titres = sum(l["valeur"] for l in valorisees)
    cout_titres = sum(l["cout"] for l in valorisees)
    total = valeur_titres + liquidites

    for l in lignes:
        l["poids_pct"] = round(l["valeur"] / total * 100, 2) if l["valeur"] is not None and total else None

    return {
        "lignes": lignes,
        "liquidites": round(liquidites, 2),
        "valeur_titres": round(valeur_titres, 2),
        "cout_titres": round(cout_titres, 2),
        "plus_value": round(valeur_titres - cout_titres, 2),
        "plus_value_pct": round((valeur_titres - cout_titres) / cout_titres * 100, 2) if cout_titres else None,
        "total": round(total, 2),
        "poids_liquidites_pct": round(liquidites / total * 100, 2) if total else None,
        "sans_cours": [l["ticker"] for l in lignes if l["valeur"] is None],
    }
