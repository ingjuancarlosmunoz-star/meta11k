"""
App: Bodegas potenciales a la redonda
Cuenta cuántas bodegas hay alrededor de tu ubicación actual en anillos de 1 km, 2 km, 3 km...
Fuente de datos: OpenStreetMap (Overpass API) o tu propio CSV de bodegas.
"""

import math

import folium
import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium
from streamlit_geolocation import streamlit_geolocation

st.set_page_config(page_title="Bodegas potenciales", page_icon="🏪", layout="wide")

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Tipos de tienda de OpenStreetMap que se consideran "bodega"
TIPOS_OSM = {
    "convenience": "Bodega / minimarket",
    "general": "Tienda general",
    "supermarket": "Supermercado",
    "kiosk": "Kiosko",
    "greengrocer": "Verdulería",
    "butcher": "Carnicería",
    "bakery": "Panadería",
    "beverages": "Licorería / bebidas",
    "variety_store": "Bazar",
}
TIPOS_DEFAULT = ["convenience", "general", "kiosk"]

COLORES = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e",
           "#e6ab02", "#a6761d", "#666666", "#1f78b4", "#b2df8a"]


# ---------------------------------------------------------------- utilidades
def haversine_km(lat1, lon1, lat2, lon2):
    """Distancia en km entre un punto y arrays de puntos."""
    r = 6371.0088
    lat1, lon1 = np.radians(lat1), np.radians(lon1)
    lat2, lon2 = np.radians(lat2), np.radians(lon2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


@st.cache_data(ttl=3600, show_spinner=False)
def buscar_osm(lat: float, lon: float, radio_km: float, tipos: tuple) -> pd.DataFrame:
    """Consulta Overpass por tiendas dentro del radio. Cacheado 1 hora."""
    radio_m = int(radio_km * 1000)
    regex = "|".join(tipos)
    query = f"""
    [out:json][timeout:90];
    (
      node["shop"~"^({regex})$"](around:{radio_m},{lat},{lon});
      way["shop"~"^({regex})$"](around:{radio_m},{lat},{lon});
    );
    out center tags;
    """
    ultimo_error = None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, timeout=120,
                              headers={"User-Agent": "bodegas-potenciales-streamlit"})
            r.raise_for_status()
            elementos = r.json().get("elements", [])
            filas = []
            for e in elementos:
                la = e.get("lat") or e.get("center", {}).get("lat")
                lo = e.get("lon") or e.get("center", {}).get("lon")
                if la is None or lo is None:
                    continue
                tags = e.get("tags", {})
                direccion = " ".join(
                    x for x in [tags.get("addr:street"), tags.get("addr:housenumber")] if x
                )
                filas.append({
                    "nombre": tags.get("name", "(sin nombre)"),
                    "tipo": TIPOS_OSM.get(tags.get("shop"), tags.get("shop")),
                    "direccion": direccion,
                    "lat": la,
                    "lon": lo,
                })
            return pd.DataFrame(filas, columns=["nombre", "tipo", "direccion", "lat", "lon"])
        except Exception as ex:  # probar el siguiente servidor
            ultimo_error = ex
    raise RuntimeError(f"No se pudo consultar OpenStreetMap: {ultimo_error}")


def leer_csv(archivo) -> pd.DataFrame:
    """Lee un CSV/Excel con columnas de latitud y longitud (nombres flexibles)."""
    if archivo.name.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(archivo)
    else:
        df = pd.read_csv(archivo, sep=None, engine="python")
    cols = {c.lower().strip(): c for c in df.columns}
    lat_col = next((cols[c] for c in ["lat", "latitud", "latitude", "y"] if c in cols), None)
    lon_col = next((cols[c] for c in ["lon", "lng", "long", "longitud", "longitude", "x"] if c in cols), None)
    if not lat_col or not lon_col:
        raise ValueError("El archivo debe tener columnas de latitud y longitud (ej. 'lat' y 'lon').")
    nom_col = next((cols[c] for c in ["nombre", "name", "razon_social", "cliente", "bodega"] if c in cols), None)
    out = pd.DataFrame({
        "nombre": df[nom_col].astype(str) if nom_col else "(sin nombre)",
        "tipo": "Mi base",
        "direccion": df[cols["direccion"]].astype(str) if "direccion" in cols else "",
        "lat": pd.to_numeric(df[lat_col], errors="coerce"),
        "lon": pd.to_numeric(df[lon_col], errors="coerce"),
    })
    return out.dropna(subset=["lat", "lon"])


def tabla_anillos(dist_km: pd.Series, radio_max: int, paso: float) -> pd.DataFrame:
    limites = np.arange(paso, radio_max + 1e-9, paso)
    filas, previo = [], 0.0
    for lim in limites:
        en_anillo = int(((dist_km > previo) & (dist_km <= lim)).sum()) + (
            int((dist_km == 0).sum()) if previo == 0 else 0
        )
        acumulado = int((dist_km <= lim).sum())
        area = math.pi * lim ** 2
        filas.append({
            "Radio": f"{lim:g} km",
            "Anillo": f"{previo:g}–{lim:g} km",
            "Bodegas en el anillo": en_anillo,
            "Total acumulado": acumulado,
            "Densidad (bodegas/km²)": round(acumulado / area, 1),
        })
        previo = lim
    return pd.DataFrame(filas)


# ---------------------------------------------------------------- interfaz
st.title("🏪 Bodegas potenciales a la redonda")
st.caption("Encuentra cuántas bodegas hay a 1 km, 2 km, 3 km… de tu ubicación actual.")

with st.sidebar:
    st.header("1. Tu ubicación")
    st.write("Pulsa el botón y acepta el permiso de ubicación del navegador:")
    geo = streamlit_geolocation()

    usar_manual = st.checkbox("Ingresar coordenadas manualmente")
    lat_m = st.number_input("Latitud", value=-12.0464, format="%.6f", disabled=not usar_manual)
    lon_m = st.number_input("Longitud", value=-77.0428, format="%.6f", disabled=not usar_manual)

    st.header("2. Radio de búsqueda")
    radio_max = st.slider("Radio máximo (km)", min_value=1, max_value=10, value=3, step=1)
    paso = st.select_slider("Tamaño de cada anillo (km)", options=[0.5, 1.0, 2.0], value=1.0)

    st.header("3. Fuente de bodegas")
    fuente = st.radio("Datos", ["OpenStreetMap (gratis)", "Mi propio archivo (CSV/Excel)"])
    tipos_sel = TIPOS_DEFAULT
    archivo = None
    if fuente.startswith("OpenStreetMap"):
        tipos_sel = st.multiselect(
            "Tipos de negocio a contar",
            options=list(TIPOS_OSM.keys()),
            default=TIPOS_DEFAULT,
            format_func=lambda k: TIPOS_OSM[k],
        )
    else:
        archivo = st.file_uploader("Archivo con columnas lat / lon", type=["csv", "xlsx", "xls"])

    st.header("4. Clientes actuales (opcional)")
    clientes_file = st.file_uploader(
        "Sube tus clientes actuales para excluirlos y ver solo las bodegas NUEVAS",
        type=["csv", "xlsx", "xls"], key="clientes",
    )
    tolerancia_m = st.number_input("Distancia para considerar 'mismo punto' (m)", 5, 200, 30, 5)

# ---- resolver ubicación
lat = lon = None
if usar_manual:
    lat, lon = lat_m, lon_m
elif geo and geo.get("latitude") is not None:
    lat, lon = geo["latitude"], geo["longitude"]

if lat is None:
    st.info("👈 Pulsa el botón de ubicación en la barra lateral (o ingresa coordenadas manualmente) para empezar.")
    st.stop()

if geo and geo.get("accuracy") and not usar_manual:
    st.caption(f"📍 Ubicación: {lat:.6f}, {lon:.6f} · precisión ±{geo['accuracy']:.0f} m")
else:
    st.caption(f"📍 Ubicación: {lat:.6f}, {lon:.6f}")

# ---- cargar bodegas
try:
    if fuente.startswith("OpenStreetMap"):
        if not tipos_sel:
            st.warning("Selecciona al menos un tipo de negocio.")
            st.stop()
        with st.spinner("Buscando bodegas en OpenStreetMap…"):
            bodegas = buscar_osm(round(lat, 5), round(lon, 5), radio_max, tuple(sorted(tipos_sel)))
    else:
        if archivo is None:
            st.info("Sube tu archivo de bodegas en la barra lateral.")
            st.stop()
        bodegas = leer_csv(archivo)
except Exception as ex:
    st.error(str(ex))
    st.stop()

bodegas = bodegas.copy()
bodegas["dist_km"] = haversine_km(lat, lon, bodegas["lat"].values, bodegas["lon"].values) if len(bodegas) else []
bodegas = bodegas[bodegas["dist_km"] <= radio_max].sort_values("dist_km").reset_index(drop=True)

# ---- excluir clientes actuales
n_excluidas = 0
if clientes_file is not None and len(bodegas):
    try:
        clientes = leer_csv(clientes_file)
        cl_lat, cl_lon = clientes["lat"].values, clientes["lon"].values
        es_cliente = [
            (haversine_km(r.lat, r.lon, cl_lat, cl_lon) * 1000 <= tolerancia_m).any() if len(clientes) else False
            for r in bodegas.itertuples()
        ]
        bodegas["es_cliente"] = es_cliente
        n_excluidas = int(bodegas["es_cliente"].sum())
        bodegas = bodegas[~bodegas["es_cliente"]].drop(columns="es_cliente").reset_index(drop=True)
    except Exception as ex:
        st.warning(f"No se pudo leer el archivo de clientes: {ex}")

# ---- métricas
tabla = tabla_anillos(bodegas["dist_km"], radio_max, paso)

st.subheader(f"Bodegas potenciales hasta {radio_max} km")
n_cols = min(len(tabla), 5)
for inicio in range(0, len(tabla), n_cols):
    cols = st.columns(n_cols)
    for j, (_, fila) in enumerate(tabla.iloc[inicio:inicio + n_cols].iterrows()):
        cols[j].metric(
            f"A {fila['Radio']}",
            f"{fila['Total acumulado']:,}",
            f"+{fila['Bodegas en el anillo']} en el anillo" if inicio + j > 0 else None,
        )
if n_excluidas:
    st.caption(f"Se excluyeron {n_excluidas} bodegas que ya son tus clientes.")

col_mapa, col_tabla = st.columns([3, 2])

with col_mapa:
    m = folium.Map(location=[lat, lon], zoom_start=15 if radio_max <= 1 else 14 if radio_max <= 2 else 13,
                   control_scale=True)
    limites = np.arange(paso, radio_max + 1e-9, paso)
    for i, lim in enumerate(limites[::-1]):
        folium.Circle(
            [lat, lon], radius=lim * 1000, color=COLORES[(len(limites) - 1 - i) % len(COLORES)],
            weight=2, fill=False, tooltip=f"{lim:g} km",
        ).add_to(m)
    folium.Marker([lat, lon], tooltip="Tú estás aquí",
                  icon=folium.Icon(color="red", icon="user", prefix="fa")).add_to(m)
    for r in bodegas.itertuples():
        idx = min(int(np.ceil(r.dist_km / paso)) - 1, len(limites) - 1) if r.dist_km > 0 else 0
        folium.CircleMarker(
            [r.lat, r.lon], radius=4, color=COLORES[idx % len(COLORES)], fill=True, fill_opacity=0.9,
            tooltip=f"{r.nombre} · {r.tipo} · {r.dist_km*1000:.0f} m",
        ).add_to(m)
    st_folium(m, height=560, use_container_width=True, returned_objects=[])

with col_tabla:
    st.markdown("**Resumen por anillo**")
    st.dataframe(tabla, hide_index=True, width="stretch")
    st.bar_chart(tabla.set_index("Radio")["Bodegas en el anillo"])

st.subheader("Listado de bodegas")
listado = bodegas.assign(**{"Distancia (m)": (bodegas["dist_km"] * 1000).round(0).astype(int)})
listado = listado[["nombre", "tipo", "direccion", "Distancia (m)", "lat", "lon"]]
st.dataframe(listado, hide_index=True, width="stretch")
st.download_button("⬇️ Descargar listado (CSV)", listado.to_csv(index=False).encode("utf-8-sig"),
                   file_name="bodegas_potenciales.csv", mime="text/csv")

if fuente.startswith("OpenStreetMap"):
    st.caption("Datos © colaboradores de OpenStreetMap. La cobertura de bodegas en OSM puede estar incompleta; "
               "para mayor precisión sube tu propia base de bodegas.")
