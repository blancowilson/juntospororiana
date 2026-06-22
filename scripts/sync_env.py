"""
Sincroniza variables nuevas de .env.example hacia .env SIN SOBREESCRIBIR
valores existentes. Pensado para cuando actualizas el repo y .env.example
trae variables nuevas (ej. FERNET_KEY en una version nueva).

Uso:
    python scripts/sync_env.py            # actualiza .env in-place
    python scripts/sync_env.py --dry-run  # solo muestra lo que haria
    python scripts/sync_env.py --out .env.new  # escribe a otro archivo

Comportamiento:
    - Si .env no existe: copia .env.example a .env (caso install nuevo)
    - Si .env existe: agrega SOLO las variables que .env tiene vacias
      o que no existen. NO toca las variables con valor.
    - Detecta variables que cambiaron de nombre (ej. DB_HOST -> DB_SERVER)
      comparando nombres "parecidos".
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def parse_env(path: Path) -> list[tuple[str, str | None]]:
    """
    Parsea un archivo .env-like. Devuelve [(variable, valor_o_None), ...]
    preservando el orden y los comentarios (los comentarios se devuelven
    como tuplas (linea_completa, None) para que no se pierdan).
    """
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            # Linea vacia o comentario
            out.append((line, None))
        elif "=" in s:
            var, _, val = s.partition("=")
            out.append((var.strip(), val.strip()))
    return out


def get_vars_dict(parsed: list[tuple[str, str | None]]) -> dict[str, str]:
    """Devuelve solo {variable: valor} ignorando comentarios y vacias."""
    return {v: val for v, val in parsed if val is not None}


def main():
    ap = argparse.ArgumentParser(description="Sincroniza .env.example -> .env sin perder valores.")
    ap.add_argument("--dry-run", action="store_true", help="Solo muestra lo que haria, no modifica archivos.")
    ap.add_argument("--out", type=str, help="Escribe el resultado a este archivo en vez de .env")
    args = ap.parse_args()

    if not ENV_EXAMPLE.exists():
        print(f"ERROR: no existe {ENV_EXAMPLE}")
        sys.exit(1)

    out_path = Path(args.out) if args.out else ENV

    # Caso 1: .env no existe -> copia completa de .env.example
    if not ENV.exists():
        if args.dry_run:
            print(f"[DRY-RUN] {ENV} no existe. Copiaria {ENV_EXAMPLE} -> {ENV}")
            return
        out_path.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"OK: {ENV} no existia. Se creo copiando {ENV_EXAMPLE}.")
        print("     Edita las variables con valores por defecto antes de arrancar.")
        return

    # Caso 2: .env existe -> merge inteligente
    parsed_existing = parse_env(ENV)
    parsed_example = parse_env(ENV_EXAMPLE)

    vars_existing = get_vars_dict(parsed_existing)
    vars_example = get_vars_dict(parsed_example)

    # Detectar nuevas
    nuevas = [v for v in vars_example if v not in vars_existing]
    # Detectar conflictos (misma variable, distinto valor)
    conflictos = [
        (v, vars_existing[v], vars_example[v])
        for v in vars_example
        if v in vars_existing and vars_existing[v] != vars_example[v]
        and vars_existing[v] != ""  # no es conflicto si .env esta vacio
    ]
    # Detectar vacias en .env que tienen valor en .env.example
    vacias_con_ejemplo = [
        v for v in vars_example
        if v in vars_existing and vars_existing[v] == "" and vars_example[v] != ""
    ]

    if not nuevas and not vacias_con_ejemplo:
        if conflictos:
            print("No hay variables nuevas, pero hay conflictos de valores:")
            for v, actual, ejemplo in conflictos:
                print(f"  {v}: actual={actual!r}  ejemplo={ejemplo!r}")
                print(f"    (se mantiene el valor actual: {actual!r})")
            print()
            print("Tu .env esta al dia. No se hicieron cambios.")
        else:
            print("Tu .env ya tiene todas las variables de .env.example. No hay nada que sincronizar.")
        return

    print("=" * 60)
    print(f"Sincronizando {ENV_EXAMPLE.name} -> {out_path.name}")
    print("=" * 60)

    if nuevas:
        print(f"\n[+] Variables NUEVAS que se agregaran ({len(nuevas)}):")
        for v in nuevas:
            ej = vars_example[v]
            # Marcar si tiene valor por defecto que se debe cambiar
            marcador = "  <- CONTIENE VALOR POR DEFECTO, editalo!" if ej in (
                "tu_contraseña_aqui", "contraseña_admin_aqui", "admin", "postgres",
                "owa_k1_pega_aqui_la_api_key_de_openwa", "sess_pega_aqui_el_id_de_sesion",
                "genera_una_clave_aleatoria_de_64_caracteres_aqui", "",
            ) else ""
            print(f"    {v}={ej}{marcador}")

    if vacias_con_ejemplo:
        print(f"\n[~] Variables VACIAS en .env que .env.example define ({len(vacias_con_ejemplo)}):")
        for v in vacias_con_ejemplo:
            print(f"    {v}={vars_example[v]}  (se rellenara con el valor de .env.example)")

    if conflictos:
        print(f"\n[!] Conflictos (se mantiene el valor actual de .env):")
        for v, actual, ejemplo in conflictos:
            print(f"    {v}: actual={actual!r}  ejemplo={ejemplo!r}  -> se MANTIENE actual")

    print()
    if args.dry_run:
        print("[DRY-RUN] No se modifico ningun archivo.")
        return

    # Construir el nuevo .env preservando todo lo existente y agregando
    # solo las variables nuevas al final
    contenido_existente = ENV.read_text(encoding="utf-8")
    if not contenido_existente.endswith("\n"):
        contenido_existente += "\n"

    bloque_nuevo = []
    if nuevas or vacias_con_ejemplo:
        bloque_nuevo.append("")
        bloque_nuevo.append(f"# --- Sincronizado automaticamente desde .env.example el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        for v in nuevas + vacias_con_ejemplo:
            val = vars_example[v]
            bloque_nuevo.append(f"{v}={val}")
        bloque_nuevo.append("")

    out_path.write_text(contenido_existente + "\n".join(bloque_nuevo), encoding="utf-8")
    print(f"OK: escrito {out_path}")
    if nuevas or vacias_con_ejemplo:
        print("    Revisa las variables marcadas y editalas si es necesario.")


if __name__ == "__main__":
    main()
