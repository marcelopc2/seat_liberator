import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit as st
from settings import API_TOKEN, BASE_URL
from functions import parse_course_ids, fetch_canvas_api

# ----------------------------
# Lecturas Canvas (SIN CACHÉ)
# ----------------------------
def get_course(course_id: str) -> dict | None:
    # include[]=account para tener account_id y luego nombre de Diplomado
    return fetch_canvas_api(f"/courses/{course_id}", params={"include[]": "account"})

def get_account(account_id: int | str) -> dict | None:
    return fetch_canvas_api(f"/accounts/{account_id}")

def get_enrollments(course_id: str) -> list[dict] | None:
    # Trae TODAS las matrículas (no filtramos por tipo para capturar roles personalizados)
    return fetch_canvas_api(f"/courses/{course_id}/enrollments", params={"per_page": 100})

# ----------------------------
# Lógica de agregación por curso
# ----------------------------
def summarize_course(course_id: str) -> dict:
    course = get_course(course_id)
    if not course:
        return {
            "id": int(course_id),
            "Curso": "NO ENCONTRADO",
            "Diplomado": "",
            "Activos": 0,
            "Completados": 0,
            "Otros Estados": 0,
            "Otros Roles": ""
        }

    course_name = course.get("name", f"Curso {course_id}")
    account_name = ""
    account_id = course.get("account_id")
    if account_id:
        acc = get_account(account_id)
        if acc:
            account_name = acc.get("name", f"Account {account_id}")

    enrollments = get_enrollments(course_id) or []

    active_students = 0
    completed_students = 0
    other_states_students = 0
    other_roles_counter = {}

    for enr in enrollments:
        enr_type = enr.get("type")                  # StudentEnrollment, TeacherEnrollment, etc.
        enr_state = enr.get("enrollment_state")     # active, inactive, completed, invited, etc.
        role_name = enr.get("role") or enr_type or "Otro"
        user = enr.get("user", {})
        user_name = user.get("name", "Sin nombre")
        
        # ⛔️ excluir Test Student completamente
        if user_name.lower() == "test student" or enr_type == "StudentViewEnrollment":
            continue

        if enr_type == "StudentEnrollment":
            if enr_state == "active":
                active_students += 1
            elif enr_state == "completed":
                completed_students += 1
            else:
                other_states_students += 1
        else:
            other_roles_counter[role_name] = other_roles_counter.get(role_name, 0) + 1

    # Crear string para otros roles
    otros_roles_str = " · ".join(f"{k}: {v}" for k, v in sorted(other_roles_counter.items())) if other_roles_counter else ""

    return {
        "id": int(course_id),
        "Curso": course_name,
        "Diplomado": account_name,
        "Activos": active_students,
        "Completados": completed_students,
        "Otros Estados": other_states_students,
        "Otros Roles": otros_roles_str
    }

# ----------------------------
# Función para obtener detalles detallados de estudiantes
# ----------------------------
def get_detailed_student_info(course_id: str) -> dict:
    course = get_course(course_id)
    if not course:
        return {
            "id": int(course_id),
            "Curso": "NO ENCONTRADO",
            "Diplomado": "",
            "Estudiantes Activos": [],
            "Estudiantes Completados": [],
            "Estudiantes Otros Estados": [],
            "Otros Roles": []
        }

    course_name = course.get("name", f"Curso {course_id}")
    account_name = ""
    account_id = course.get("account_id")
    if account_id:
        acc = get_account(account_id)
        if acc:
            account_name = acc.get("name", f"Account {account_id}")

    enrollments = get_enrollments(course_id) or []

    active_students = []
    completed_students = []
    other_states_students = []
    other_roles = []

    for enr in enrollments:
        enr_type = enr.get("type")
        enr_state = enr.get("enrollment_state")
        role_name = enr.get("role") or enr_type or "Otro"
        user = enr.get("user", {})
        user_name = user.get("name", "Sin nombre")
        user_email = user.get("login_id", user.get("email", "Sin email"))
        user_id = user.get("id", "Sin ID")
        
        # ⛔️ excluir Test Student completamente
        if user_name.lower() == "test student" or enr_type == "StudentViewEnrollment":
            continue

        student_info = {
            "Nombre": user_name,
            "Email": user_email,
            "User ID": user_id,
            "Estado": enr_state,
            "Rol": role_name
        }

        if enr_type == "StudentEnrollment":
            if enr_state == "active":
                active_students.append(student_info)
            elif enr_state == "completed":
                completed_students.append(student_info)
            else:
                other_states_students.append(student_info)
        else:
            other_roles.append(student_info)

    return {
        "id": int(course_id),
        "Curso": course_name,
        "Diplomado": account_name,
        "Estudiantes Activos": active_students,
        "Estudiantes Completados": completed_students,
        "Estudiantes Otros Estados": other_states_students,
        "Otros Roles": other_roles
    }

# ----------------------------
# Procesamiento de cursos
# ----------------------------
def process_courses(course_ids: list[str], max_workers: int = 8) -> pd.DataFrame:
    rows = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(summarize_course, cid): cid for cid in course_ids}
        for fut in as_completed(futures):
            rows.append(fut.result())

    rows.sort(key=lambda x: x["id"])
    df = pd.DataFrame(rows, columns=[
        "id", "Curso", "Diplomado", "Activos", "Completados", "Otros Estados", "Otros Roles"
    ])
    df.index = range(1, len(df) + 1)
    df.index.name = "#"
    return df

def process_detailed_courses(course_ids: list[str], max_workers: int = 4) -> list[dict]:
    detailed_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(get_detailed_student_info, cid): cid for cid in course_ids}
        for fut in as_completed(futures):
            detailed_results.append(fut.result())
    
    detailed_results.sort(key=lambda x: x["id"])
    return detailed_results

# ----------------------------
# UI Streamlit
# ----------------------------
st.set_page_config(page_title="Student Seat Liberator", layout="wide", page_icon="🪑")
st.title("🪑 Student Seat Liberator".upper())
st.caption("Ingresa uno o más **IDs de curso** de Canvas (separados por coma, espacio o salto de línea).")

ids_input = st.text_area(
    "IDs de curso",
    placeholder="Ej: 12345, 67890\n112233\n445566 778899",
    height=120
)

# max_workers = st.slider("Hilos paralelos", min_value=1, max_value=16, value=8)
max_workers = 8
show_details = st.checkbox("Mostrar detalles completos de estudiantes", value=False)

run_btn = st.button("Buscar")

if run_btn:
    course_ids = parse_course_ids(ids_input)
    if not course_ids:
        st.warning("Por favor, ingresa al menos un ID de curso válido (solo números).")
        st.stop()

    with st.spinner("Consultando..."):
        try:
            if show_details:
                detailed_results = process_detailed_courses(course_ids, max_workers=max_workers)
            else:
                df = process_courses(course_ids, max_workers=max_workers)
        except Exception as e:
            st.error(f"Ocurrió un error consultando Canvas: {e}")
            st.stop()

    if show_details:
        for result in detailed_results:
            st.subheader(f"Curso: {result['Curso']} (ID: {result['id']})")
            st.write(f"**Diplomado:** {result['Diplomado']}")
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("Activos", len(result['Estudiantes Activos']))
            with col2:
                st.metric("Completados", len(result['Estudiantes Completados']))
            with col3:
                st.metric("Otros Estados", len(result['Estudiantes Otros Estados']))
            with col4:
                st.metric("Otros Roles", len(result['Otros Roles']))
            
            # Mostrar tablas detalladas
            tabs = st.tabs(["Activos", "Completados", "Otros Estados", "Otros Roles"])
            
            with tabs[0]:
                if result['Estudiantes Activos']:
                    df_activos = pd.DataFrame(result['Estudiantes Activos'])
                    st.dataframe(df_activos, use_container_width=True)
                else:
                    st.info("No hay estudiantes activos")
            
            with tabs[1]:
                if result['Estudiantes Completados']:
                    df_completados = pd.DataFrame(result['Estudiantes Completados'])
                    st.dataframe(df_completados, use_container_width=True)
                else:
                    st.info("No hay estudiantes completados")
            
            with tabs[2]:
                if result['Estudiantes Otros Estados']:
                    df_otros_estados = pd.DataFrame(result['Estudiantes Otros Estados'])
                    st.dataframe(df_otros_estados, use_container_width=True)
                else:
                    st.info("No hay estudiantes con otros estados")
            
            with tabs[3]:
                if result['Otros Roles']:
                    df_otros_roles = pd.DataFrame(result['Otros Roles'])
                    st.dataframe(df_otros_roles, use_container_width=True)
                else:
                    st.info("No hay otros roles")
            
            st.divider()
    else:
        st.success(f"Se procesaron {len(df)} cursos.")
        st.dataframe(df, use_container_width=True)
        
        # Mostrar resumen total
        total_activos = df["Activos"].sum()
        total_completados = df["Completados"].sum()
        total_otros_estados = df["Otros Estados"].sum()
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Activos", total_activos)
        with col2:
            st.metric("Total Completados", total_completados)
        with col3:
            st.metric("Total Otros Estados", total_otros_estados)