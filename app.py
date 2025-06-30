import streamlit as st
import pymongo
from datetime import datetime, timezone, timedelta
from PIL import Image
import io, base64
import pytz
import time
import getpass
import os

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="🧹 Visualizador de Limpieza", layout="centered")
st.title("🧹 Visualizador de Limpieza")

# --- CONEXIÓN A MONGO ---
MONGO_URI = os.environ["MONGO_URI"]
client = pymongo.MongoClient(MONGO_URI)
db = client.cleanup
collection = db.entries
meta = db.meta
CO = pytz.timezone("America/Bogota")

# --- Inicialización del documento meta (por si acaso) ---
if meta.count_documents({}) == 0:
    meta.insert_one({"ultimo_pellizco_global": {}})

# --- FUNCIONES AUXILIARES ---
def resize_image(img, max_width=300):
    img = img.convert("RGB")
    w, h = img.size
    if w > max_width:
        ratio = max_width / w
        img = img.resize((int(w * ratio), int(h * ratio)))
    return img

def image_to_base64(img):
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=40, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode()

def base64_to_image(b64_str):
    try:
        img = Image.open(io.BytesIO(base64.b64decode(b64_str)))
        return img.convert("RGB")
    except Exception:
        return Image.new("RGB", (300, 200), color="gray")

def simple_edge_score(img):
    grayscale = img.convert("L")
    pixels = list(grayscale.getdata())
    diffs = [abs(pixels[i] - pixels[i+1]) for i in range(len(pixels)-1)]
    return sum(d > 10 for d in diffs)

def format_seconds(seconds):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02}:{m:02}:{s:02}"

def agrega_pellizco(session_id, user, mensaje):
    collection.update_one(
        {"_id": session_id},
        {"$push": {
            "meta.pellizcos": {
                "user": user,
                "datetime": datetime.now(timezone.utc),
                "mensaje": mensaje
            }
        }},
        upsert=True
    )

def actualiza_meta_global(user, mensaje):
    meta.update_one(
        {},
        {"$set": {
            "ultimo_pellizco_global": {
                "user": user,
                "datetime": datetime.now(timezone.utc),
                "mensaje": mensaje
            }
        }},
        upsert=True
    )

# --- BLOQUE DE SINCRONIZACIÓN GLOBAL ---
if "ultimo_pellizco_global" not in st.session_state:
    st.session_state.ultimo_pellizco_global = None

meta_doc = meta.find_one({}) or {}
nuevo_pellizco = meta_doc.get("ultimo_pellizco_global", {})
if nuevo_pellizco != st.session_state.ultimo_pellizco_global:
    st.session_state.ultimo_pellizco_global = nuevo_pellizco
    st.rerun()

# --- SESSION STATE USUARIOS ---
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "ultimo_pellizco" not in st.session_state:
    st.session_state.ultimo_pellizco = None
if "user_login" not in st.session_state:
    st.session_state.user_login = getpass.getuser()

# --- UI PRINCIPAL ---
st.subheader("Registro de Sesión de Limpieza")

estado = None
last = collection.find_one({"session_active": True}) or collection.find_one(sort=[("start_time", -1)])

if last and last.get("session_active"):
    estado = "activa"
elif last and not last.get("session_active") and not last.get("image_after"):
    estado = "esperando_despues"
else:
    estado = "sin_sesion"

# --- SESIÓN ACTIVA ---
if estado == "activa":
    st.success(f"Sesión activa iniciada por: {last['meta']['pellizcos'][0]['user']}")
    col1, col2 = st.columns([1, 2])
    with col1:
        st.image(base64_to_image(last.get("image_base64", "")), caption="ANTES", width=200)
        before_edges = last.get("edges", 0)
        st.markdown(f"**Saturación visual antes:** `{before_edges:,}`")
    with col2:
        st.info("Cuando termines la limpieza, detén el cronómetro para finalizar la sesión.")
        # Cronómetro
        start_time = last["start_time"].astimezone(CO)
        elapsed = int((datetime.now(CO) - start_time).total_seconds())
        cronometro = st.empty()
        stop = st.button("⏹️ Detener y Finalizar sesión", type="primary", use_container_width=True)
        for i in range(elapsed, elapsed + 100000):
            cronometro.markdown(f"### 🕒 Tiempo activo: {str(timedelta(seconds=i))}")
            time.sleep(1)
            if stop:
                end_time = datetime.now(timezone.utc)
                duration = int((end_time - last['start_time']).total_seconds())
                collection.update_one(
                    {"_id": last["_id"], "session_active": True},
                    {"$set": {
                        "session_active": False,
                        "end_time": end_time,
                        "duration_seconds": duration,
                        "improved": None
                    }}
                )
                agrega_pellizco(last["_id"], st.session_state.user_login, "Sesión finalizada, esperando DESPUÉS")
                actualiza_meta_global(st.session_state.user_login, "Sesión finalizada, esperando DESPUÉS")
                st.success("¡Sesión finalizada! Ahora sube la foto del después cuando quieras.")
                st.rerun()
            time.sleep(1)

# --- ESPERANDO FOTO DESPUÉS ---
elif estado == "esperando_despues":
    st.info("Sesión finalizada. Sube la foto del DESPUÉS para completar el registro.")
    st.image(base64_to_image(last.get("image_base64", "")), caption="ANTES (guardado)", width=220)
    img_after_file = st.file_uploader("Sube la foto del DESPUÉS", type=["jpg", "jpeg", "png"], key="after", label_visibility="visible")
    if img_after_file is not None:
        with st.spinner("Guardando foto del después..."):
            try:
                img_after = Image.open(img_after_file)
                resized_after = resize_image(img_after)
                img_b64_after = image_to_base64(resized_after)
                edges_after = simple_edge_score(resized_after)
                improved = False
                edges_before = last.get("edges", 0)
                if edges_before:
                    improved = edges_after < edges_before * 0.9
                collection.update_one(
                    {"_id": last["_id"]},
                    {"$set": {
                        "image_after": img_b64_after,
                        "edges_after": edges_after,
                        "improved": improved
                    }}
                )
                agrega_pellizco(last["_id"], st.session_state.user_login, "Se subió el DESPUÉS")
                actualiza_meta_global(st.session_state.user_login, "Se subió el DESPUÉS")
                st.success("¡Foto del después registrada exitosamente!")
                st.rerun()
            except Exception as e:
                import traceback
                st.error(f"Error al guardar la foto del después: {e}")
                st.text(traceback.format_exc())
    st.info("Cuando subas la foto del después, se completará la sesión en el historial.")

# --- SIN SESIÓN ACTIVA ---
else:
    st.info("No hay sesión activa. Inicia una nueva sesión subiendo una foto de ANTES.")
    img_file = st.file_uploader("Sube la foto del ANTES", type=["jpg", "jpeg", "png"], key="before_new")
    if img_file:
        img = Image.open(img_file)
        resized = resize_image(img)
        img_b64 = image_to_base64(resized)
        edges = simple_edge_score(resized)
        now_utc = datetime.now(timezone.utc)
        session = collection.insert_one({
            "session_active": True,
            "start_time": now_utc,
            "image_base64": img_b64,
            "edges": edges,
            "meta": {
                "pellizcos": [{
                    "user": st.session_state.user_login,
                    "datetime": now_utc,
                    "mensaje": "Se subió el ANTES"
                }]
            }
        })
        agrega_pellizco(session.inserted_id, st.session_state.user_login, "Se subió el ANTES")
        actualiza_meta_global(st.session_state.user_login, "Se subió el ANTES")
        st.success("¡Sesión iniciada! Cuando termines, detén el cronómetro.")
        st.rerun()

# --- HISTORIAL ---
st.subheader("🗂️ Historial de Sesiones")

registros = list(collection.find({"session_active": False}).sort("start_time", -1).limit(30))
if registros:
    data = []
    for r in registros:
        inicio = r["start_time"].astimezone(CO).strftime("%Y-%m-%d %H:%M:%S")
        fin = r.get("end_time")
        if isinstance(fin, datetime):
            fin = fin.astimezone(CO).strftime("%Y-%m-%d %H:%M:%S")
        else:
            fin = "—"
        dur = r.get("duration_seconds", 0)
        edges_before = r.get('edges', 0)
        edges_after = r.get('edges_after', 0)
        diff = edges_before - edges_after
        mejora = ""
        if diff > 0:
            mejora = f"⬇️ -{diff:,}"
        elif diff < 0:
            mejora = f"⬆️ +{abs(diff):,}"
        else:
            mejora = "= 0"
        data.append({
            "Inicio": inicio,
            "Fin": fin,
            "Duración": format_seconds(dur),
            "Saturación antes": edges_before,
            "Saturación después": edges_after if edges_after else "—",
            "Diferencia": mejora,
            "¿Mejoró?": "✅ Sí" if r.get('improved') else "❌ No"
        })
    st.dataframe(data, use_container_width=True)
else:
    st.info("No hay registros finalizados.")

with st.expander("🧨 Borrar todos los registros"):
    st.warning("¡Esta acción eliminará todo el historial! No se puede deshacer.")
    if st.button("🗑️ Borrar todo", use_container_width=True):
        now_utc = datetime.now(timezone.utc)
        collection.delete_many({})
        actualiza_meta_global(st.session_state.user_login, "Se borraron todos los registros")
        st.success("Registros eliminados.")
        st.rerun()
