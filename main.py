import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit as st
from io import BytesIO

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

def get_enrollments_with_user(course_id: str) -> list[dict] | None:
    # Igual que arriba pero con datos de usuario para exportar detalle
    return fetch_canvas_api(
        f"/courses/{course_id}/enrollments",
        params={"per_page": 100, "include[]": "user"}
    )

# ----------------------------
# Lógica de agregación por curso (resumen)
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
        user = enr.get("user", {})                 # puede venir vacío si no pedimos include[]=user
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
# Detalle de matrículas (para exportar)
# ----------------------------
def build_enrollments_detail_df(course_ids: list[str]) -> pd.DataFrame:
    """
    Retorna un DF con TODAS las matrículas (estudiantes y otros roles) por curso/diplomado.
    Excluye Test Student (StudentViewEnrollment o nombre 'Test Student').
    Columnas:
      id_curso, Curso, Diplomado, user_id, nombre, login_id, sis_user_id,
      tipo_matricula, rol, estado, seccion_id, enrollment_id, created_at, updated_at, last_activity_at
    """
    rows = []
    # caché simple curso / cuenta para no repetir llamadas
    course_cache = {}
    account_cache = {}

    for cid in course_ids:
        # curso y diplomado
        if cid not in course_cache:
            c = get_course(cid) or {}
            course_cache[cid] = {"name": c.get("name", f"Curso {cid}"), "account_id": c.get("account_id")}
            acc_id = c.get("account_id")
            if acc_id and acc_id not in account_cache:
                a = get_account(acc_id) or {}
                account_cache[acc_id] = a.get("name", f"Account {acc_id}")

        curso_name = course_cache[cid]["name"]
        diplomado_name = account_cache.get(course_cache[cid]["account_id"], "")

        enrollments = (get_enrollments_with_user(cid) or [])
        for e in enrollments:
            etype = e.get("type")
            role = e.get("role") or etype
            state = e.get("enrollment_state")
            section_id = e.get("course_section_id")
            u = e.get("user") or {}
            uname = (u.get("name") or "").strip()
            # excluir Test Student
            if etype == "StudentViewEnrollment" or uname.lower() == "test student":
                continue

            rows.append({
                "id_curso": int(cid),
                "Curso": curso_name,
                "Diplomado": diplomado_name,
                "user_id": u.get("id"),
                "nombre": uname,
                "login_id": u.get("login_id"),
                "sis_user_id": u.get("sis_user_id"),
                "tipo_matricula": etype,
                "rol": role,
                "estado": state,
                "seccion_id": section_id,
                "enrollment_id": e.get("id"),
                "created_at": e.get("created_at"),
                "updated_at": e.get("updated_at"),
                "last_activity_at": e.get("last_activity_at"),
            })

    df_detail = pd.DataFrame(rows)
    if not df_detail.empty:
        cols = [
            "id_curso","Curso","Diplomado","user_id","nombre","login_id","sis_user_id",
            "tipo_matricula","rol","estado","seccion_id","enrollment_id",
            "created_at","updated_at","last_activity_at"
        ]
        df_detail = df_detail[cols]
        df_detail.sort_values(["id_curso","tipo_matricula","estado","nombre"], inplace=True)
        df_detail.reset_index(drop=True, inplace=True)
    return df_detail

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
# (Opcional) para la vista detallada por pantalla
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

    enrollments = get_enrollments_with_user(course_id) or []

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

        # ⛔️ excluir Test Student
        if user_name.strip().lower() == "test student" or enr_type == "StudentViewEnrollment":
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
show_details = st.checkbox("Mostrar detalle de estudiantes", value=False)

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
                st.session_state["detailed_results"] = detailed_results
                st.session_state["df_resumen"] = process_courses(course_ids, max_workers=max_workers)
            else:
                df = process_courses(course_ids, max_workers=max_workers)
                st.session_state["df_resumen"] = df
            st.session_state["course_ids"] = course_ids
        except Exception as e:
            st.error(f"Ocurrió un error consultando Canvas: {e}")
            st.stop()

# Mostrar resultados si existen
course_ids = st.session_state.get("course_ids", [])
df_resumen = st.session_state.get("df_resumen", None)

if df_resumen is not None and course_ids:
    if show_details:
        detailed_results = st.session_state.get("detailed_results", [])
        for result in detailed_results:
            st.subheader(f"Curso: {result['Curso']} (ID: {result['id']})")
            st.write(f"**Diplomado:** {result['Diplomado']}")

            col1, col2, col3, col4 = st.columns(4)
            with col1: st.metric("Activos", len(result['Estudiantes Activos']))
            with col2: st.metric("Completados", len(result['Estudiantes Completados']))
            with col3: st.metric("Otros Estados", len(result['Estudiantes Otros Estados']))
            with col4: st.metric("Otros Roles", len(result['Otros Roles']))

            tabs = st.tabs(["Activos", "Completados", "Otros Estados", "Otros Roles"])
            with tabs[0]:
                st.dataframe(pd.DataFrame(result['Estudiantes Activos']) if result['Estudiantes Activos'] else pd.DataFrame(), use_container_width=True)
            with tabs[1]:
                st.dataframe(pd.DataFrame(result['Estudiantes Completados']) if result['Estudiantes Completados'] else pd.DataFrame(), use_container_width=True)
            with tabs[2]:
                st.dataframe(pd.DataFrame(result['Estudiantes Otros Estados']) if result['Estudiantes Otros Estados'] else pd.DataFrame(), use_container_width=True)
            with tabs[3]:
                st.dataframe(pd.DataFrame(result['Otros Roles']) if result['Otros Roles'] else pd.DataFrame(), use_container_width=True)
            st.divider()
    else:
        st.success(f"Se procesaron {len(df_resumen)} cursos.")
        st.dataframe(df_resumen, use_container_width=True)

        total_activos = df_resumen["Activos"].sum()
        total_completados = df_resumen["Completados"].sum()
        total_otros_estados = df_resumen["Otros Estados"].sum()

        col1, col2, col3 = st.columns(3)
        with col1: st.metric("Total Activos", total_activos)
        with col2: st.metric("Total Completados", total_completados)
        with col3: st.metric("Total Otros Estados", total_otros_estados)

    # ----------------------------
    # Exportar a Excel (Resumen + Matrículas)
    # ----------------------------
    st.divider()
    # st.subheader("Exportar")

    if st.button("⬇️ Generar Reporte", use_container_width=True):
        with st.spinner("Generando Excel..."):
            df_detail = build_enrollments_detail_df(course_ids)

            # Si no hay detalle, generamos igual archivo con hojas vacías estructuradas
            output = BytesIO()
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                # Hoja 1: Resumen
                df_resumen.to_excel(writer, index=False, sheet_name="Resumen")

                # Hoja 2: Detalle de Matrículas
                if df_detail.empty:
                    pd.DataFrame(columns=[
                        "id_curso","Curso","Diplomado","user_id","nombre","login_id","sis_user_id",
                        "tipo_matricula","rol","estado","seccion_id","enrollment_id",
                        "created_at","updated_at","last_activity_at"
                    ]).to_excel(writer, index=False, sheet_name="Matriculas")
                else:
                    df_detail.to_excel(writer, index=False, sheet_name="Matriculas")

            output.seek(0)

        st.download_button(
            "📥 Descargar Reporte",
            data=output,
            file_name="matriculas_por_curso.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

elif not run_btn and ids_input:
    st.info("Presiona el botón 'Buscar' para consultar los cursos")
