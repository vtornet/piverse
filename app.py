from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from flask_babel import Babel, gettext as _, lazy_gettext as _l, get_locale as get_babel_locale, \
                        format_datetime, format_date, format_time, format_timedelta, format_number
import sqlite3
import os
import re
import requests # Para hacer peticiones HTTP
import urllib.parse # Para construir URLs absolutas para las imágenes
from bs4 import BeautifulSoup # Para analizar HTML
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timezone

app = Flask(__name__)
app.secret_key = 'tu_clave_secreta_aqui_MUY_SECRETA'

# --- Configuración de Uploads ---
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

upload_path = os.path.join(app.root_path, UPLOAD_FOLDER)
os.makedirs(upload_path, exist_ok=True)
POST_IMAGES_FOLDER = os.path.join(app.config['UPLOAD_FOLDER'], 'post_images')
os.makedirs(POST_IMAGES_FOLDER, exist_ok=True)


# --- Configuración de Babel ---
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
app.config['BABEL_DEFAULT_TIMEZONE'] = 'UTC'
app.config['LANGUAGES'] = {
    'en': 'English',
    'es': 'Español',
    'vi': 'Tiếng Việt',
    'sw': 'Kiswahili'
}
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'

def select_current_locale():
    user_lang = session.get('language')
    if user_lang and user_lang in app.config['LANGUAGES'].keys():
        return user_lang
    if request:
        return request.accept_languages.best_match(app.config['LANGUAGES'].keys())
    return app.config['BABEL_DEFAULT_LOCALE']

babel = Babel(app, locale_selector=select_current_locale)

if app.jinja_env:
    app.jinja_env.globals['format_datetime'] = format_datetime
    app.jinja_env.globals['format_date'] = format_date
    app.jinja_env.globals['format_time'] = format_time
    app.jinja_env.globals['format_timedelta'] = format_timedelta
    app.jinja_env.globals['format_number'] = format_number

def init_db():
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()

        # --- Creación de Tablas Existentes (se mantienen todas tus tablas) ---
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                username TEXT UNIQUE,
                bio TEXT,
                photo TEXT,
                slug TEXT UNIQUE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')

        # --- MODIFICACIÓN EN LA TABLA 'posts' ---
        # Se añaden las columnas para la previsualización de enlaces
        # y se incluye section_id directamente para mayor claridad.
        c.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                image_filename TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                section_id INTEGER REFERENCES sections(id) ON DELETE SET NULL,
                preview_url TEXT,
                preview_title TEXT,
                preview_description TEXT,
                preview_image_url TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')

        # --- SALVAGUARDAS PARA LA TABLA 'posts' ---
        # Añadir section_id si falta (por si la BD es de una versión muy anterior)
        c.execute("PRAGMA table_info(posts)")
        post_columns = [column[1] for column in c.fetchall()]
        if 'section_id' not in post_columns:
            try:
                c.execute("ALTER TABLE posts ADD COLUMN section_id INTEGER REFERENCES sections(id) ON DELETE SET NULL")
            except sqlite3.OperationalError as e:
                print(f"DEBUG: No se pudo añadir la columna 'section_id' a 'posts': {e}")

        # Añadir columnas de previsualización si faltan
        preview_cols_to_add = {
            'preview_url': 'TEXT',
            'preview_title': 'TEXT',
            'preview_description': 'TEXT',
            'preview_image_url': 'TEXT'
        }
        for col_name, col_type in preview_cols_to_add.items():
            if col_name not in post_columns:
                try:
                    c.execute(f"ALTER TABLE posts ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError as e:
                    print(f"DEBUG: No se pudo añadir la columna '{col_name}' a 'posts': {e}")
        # --- FIN DE SALVAGUARDAS PARA 'posts' ---

        c.execute('''
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                parent_comment_id INTEGER DEFAULT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(parent_comment_id) REFERENCES comments(id) ON DELETE CASCADE
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS post_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reaction_type TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(post_id, user_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS contactos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                solicitante_id INTEGER NOT NULL,
                receptor_id INTEGER NOT NULL,
                estado TEXT DEFAULT 'pendiente',
                UNIQUE(solicitante_id, receptor_id),
                FOREIGN KEY(solicitante_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(receptor_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS notificaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                mensaje TEXT NOT NULL,
                tipo TEXT,
                referencia_id INTEGER,
                leida INTEGER DEFAULT 0,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS conversation_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(conversation_id, user_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_read INTEGER DEFAULT 0,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS comment_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reaction_type TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(comment_id) REFERENCES comments(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(comment_id, user_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS shared_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_post_id INTEGER NOT NULL,
                quote_content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(original_post_id) REFERENCES posts(id) ON DELETE CASCADE,
                UNIQUE(user_id, original_post_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS blocked_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                blocker_user_id INTEGER NOT NULL,
                blocked_user_id INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(blocker_user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(blocked_user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(blocker_user_id, blocked_user_id)
            )
        ''')

        # --- SECCIÓN PARA LA TABLA 'sections' ---
        c.execute('''
            CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                description TEXT,
                icon_filename TEXT
            )
        ''')
        c.execute("PRAGMA table_info(sections)")
        existing_columns_sections = [column[1] for column in c.fetchall()]
        if 'icon_filename' not in existing_columns_sections:
            try:
                c.execute("ALTER TABLE sections ADD COLUMN icon_filename TEXT")
            except sqlite3.OperationalError as e:
                print(f"DEBUG: No se pudo añadir la columna 'icon_filename' a 'sections': {e}")

        initial_sections = [
            {'name': 'KYC (Conoce a tu Cliente)', 'slug': 'kyc', 'description': 'Discusiones sobre el proceso KYC de Pi Network.', 'icon_filename': 'kyc.png'},
            {'name': 'Círculo de Seguridad', 'slug': 'circulo-seguridad', 'description': 'Todo sobre cómo construir y mantener tu círculo de seguridad.', 'icon_filename': 'circulo-seguridad.png'},
            {'name': 'Migraciones a Mainnet', 'slug': 'migraciones-mainnet', 'description': 'Información y experiencias sobre la migración de Pi a la Mainnet.', 'icon_filename': 'migraciones-mainnet.png'},
            {'name': 'Nodos Pi', 'slug': 'nodos-pi', 'description': 'Configuración, mantenimiento y discusión sobre los Nodos de Pi.', 'icon_filename': 'nodos-pi.png'},
            {'name': 'Noticias de Pi Network', 'slug': 'noticias-pi', 'description': 'Últimas noticias y anuncios oficiales de Pi Network.', 'icon_filename': 'noticias-pi.png'},
            {'name': 'Apps del Ecosistema Pi', 'slug': 'apps-ecosistema', 'description': 'Descubre y discute aplicaciones y utilidades construidas en la red Pi.', 'icon_filename': 'apps-ecosistema.png'},
            {'name': 'Comercio y Marketplace Pi', 'slug': 'comercio-pi', 'description': 'Espacio para intercambios, y discusión sobre bienes y servicios usando Pi.', 'icon_filename': 'comercio-pi.png'},
            {'name': 'Ayuda y Soporte Técnico', 'slug': 'ayuda-soporte', 'description': '¿Necesitas ayuda con la app de Pi, wallet, o tienes dudas técnicas?', 'icon_filename': 'ayuda-soporte.png'},
            {'name': 'Debate General Pi', 'slug': 'debate-general-pi', 'description': 'Para temas generales sobre Pi Network que no encajan en otras secciones.', 'icon_filename': 'debate-general-pi.png'},
            {'name': 'PiVerse: Ideas y Sugerencias', 'slug': 'piverse-ideas', 'description': 'Feedback y sugerencias para mejorar esta plataforma, PiVerse.', 'icon_filename': 'piverse-ideas.png'}
        ]
        for section_data in initial_sections:
            c.execute("INSERT OR IGNORE INTO sections (name, slug, description, icon_filename) VALUES (?, ?, ?, ?)",
                      (section_data['name'], section_data['slug'], section_data['description'], section_data['icon_filename']))
            c.execute("UPDATE sections SET description = ?, icon_filename = ? WHERE slug = ?",
                      (section_data['description'], section_data['icon_filename'], section_data['slug']))

        conn.commit()
        
# --- FUNCIONES AUXILIARES ---

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generar_slug(nombre):
    slug = nombre.strip().lower()
    slug = re.sub(r'\s+', '_', slug)
    slug = re.sub(r'[^\w_]', '', slug)
    return slug

def get_blocked_and_blocking_ids(user_id, c):
    """
    Obtiene un conjunto de IDs de usuarios que han bloqueado al user_id actual
    o que han sido bloqueados por el user_id actual.
    Requiere un cursor de base de datos activo como argumento.
    """
    excluded_ids = set()
    if not user_id:
        return excluded_ids

    # Usuarios que el usuario actual ha bloqueado
    c.execute("SELECT blocked_user_id FROM blocked_users WHERE blocker_user_id = ?", (user_id,))
    blocked_by_me = {row[0] for row in c.fetchall()}

    # Usuarios que han bloqueado al usuario actual
    c.execute("SELECT blocker_user_id FROM blocked_users WHERE blocked_user_id = ?", (user_id,))
    blocked_me = {row[0] for row in c.fetchall()}

    excluded_ids.update(blocked_by_me)
    excluded_ids.update(blocked_me)

    return excluded_ids

def regenerar_slugs_si_faltan():
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('SELECT user_id, username, slug FROM profiles WHERE username IS NOT NULL AND username != ""')
        perfiles_a_revisar = c.fetchall()
        if not perfiles_a_revisar: return
        regenerados_count = 0
        for user_id, username_perfil, slug_actual in perfiles_a_revisar:
            if not (username_perfil and username_perfil.strip()): continue
            slug_esperado = generar_slug(username_perfil)
            if slug_actual is None or slug_actual.strip() == "" or slug_actual != slug_esperado:
                try:
                    c.execute('UPDATE profiles SET slug = ? WHERE user_id = ?', (slug_esperado, user_id))
                    if c.rowcount > 0: regenerados_count += 1
                except sqlite3.IntegrityError:
                    slug_alternativo = generar_slug(f"{username_perfil}_{user_id}")
                    try:
                        c.execute('UPDATE profiles SET slug = ? WHERE user_id = ?', (slug_alternativo, user_id))
                        if c.rowcount > 0: regenerados_count += 1
                    except sqlite3.IntegrityError as e_alt:
                        print(f"Error de integridad (slug alternativo) para user_id {user_id}: {e_alt}")
        if regenerados_count > 0:
            conn.commit()
            print(f"{regenerados_count} slugs regenerados/corregidos.")


def check_profile_completion(user_id):
    if user_id is None: return False
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute("SELECT username, slug FROM profiles WHERE user_id = ?", (user_id,))
        profile_data = c.fetchone()
        if profile_data and \
           profile_data[0] and profile_data[0].strip() and \
           profile_data[1] and profile_data[1].strip() and \
           profile_data[1] == generar_slug(profile_data[0]):
            return True
    return False

def procesar_menciones_y_notificar(texto, autor_id, id_referencia, tipo_contenido_str):
    menciones_encontradas = re.findall(r'@([a-zA-Z0-9_]+)', texto, flags=re.IGNORECASE)
    if not menciones_encontradas: return
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        excluded_ids = get_blocked_and_blocking_ids(autor_id, c)

        c.execute('SELECT slug, username FROM profiles WHERE user_id = ?', (autor_id,))
        autor_perfil = c.fetchone()
        autor_slug_enlace = autor_perfil[0] if autor_perfil and autor_perfil[0] else "#"
        autor_nombre_visible = autor_perfil[1] if autor_perfil and autor_perfil[1] else _("Usuario")

        for slug_mencionado in menciones_encontradas:
            c.execute('SELECT user_id FROM profiles WHERE lower(slug) = lower(?)', (slug_mencionado.lower(),))
            usuario_mencionado_info = c.fetchone()
            if usuario_mencionado_info and usuario_mencionado_info[0] != autor_id:
                id_usuario_mencionado = usuario_mencionado_info[0]
                if id_usuario_mencionado in excluded_ids:
                    continue # No notificar si el usuario está bloqueado

                try:
                    enlace_post_url = url_for("ver_publicacion_individual", post_id_vista=int(id_referencia))
                except ValueError:
                    enlace_post_url = "#"

                autor_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=autor_slug_enlace)}">@{autor_nombre_visible}</a>'

                if tipo_contenido_str == "publicación":
                    contenido_link_text = _("publicación")
                    contenido_link_html = f'<a href="{enlace_post_url}">{contenido_link_text}</a>'
                    mensaje_template = _('%(autor_link)s te mencionó en una %(contenido_link)s.')
                else:
                    contenido_link_text = _("comentario")
                    contenido_link_html = f'<a href="{enlace_post_url}">{contenido_link_text}</a>'
                    mensaje_template = _('%(autor_link)s te mencionó en un %(contenido_link)s.')

                mensaje = mensaje_template % {'autor_link': autor_link_html, 'contenido_link': contenido_link_html}

                c.execute('INSERT INTO notificaciones (user_id, mensaje, tipo, referencia_id) VALUES (?, ?, ?, ?)',
                          (id_usuario_mencionado, mensaje, 'mencion', id_referencia))
        conn.commit()

def procesar_menciones_para_mostrar(texto):
    if texto is None: return ""
    def reemplazar(match):
        slug_capturado = match.group(1)
        slug_enlace = slug_capturado.lower()
        return f'<a href="{url_for("ver_perfil", slug_perfil=slug_enlace)}">@{slug_capturado}</a>'
    return re.sub(r'@([a-zA-Z0-9_]+)', reemplazar, texto, flags=re.IGNORECASE)

def parse_timestamp(timestamp_str):
    if not timestamp_str:
        return None
    if isinstance(timestamp_str, datetime):
        return timestamp_str

    formats_to_try = [
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S'
    ]
    for fmt in formats_to_try:
        try:
            dt_obj = datetime.strptime(timestamp_str, fmt)
            return dt_obj
        except ValueError:
            continue
    print(f"ADVERTENCIA: No se pudo parsear la cadena de timestamp: {timestamp_str} con los formatos probados.")
    return None

# --- NUEVAS FUNCIONES PARA PREVISUALIZACIÓN DE ENLACES ---
def extract_first_url(text):
    """
    Encuentra y devuelve la primera URL HTTP/HTTPS válida en un texto.
    """
    if not text:
        return None
    url_pattern = r'https?://[^\s/$.?#].[^\s]*'
    match = re.search(url_pattern, text)
    if match:
        return match.group(0)
    return None

def generate_link_preview(url):
    """
    Intenta generar una previsualización de enlace obteniendo metadatos de la URL.
    """
    if not url:
        return None

    preview = {
        'url': url,
        'title': None,
        'description': None,
        'image_url': None
    }

    try:
        headers = {'User-Agent': 'PiVerseLinkPreviewer/1.0'}
        response = requests.get(url, headers=headers, timeout=7, allow_redirects=True)
        response.raise_for_status()

        if 'text/html' not in response.headers.get('Content-Type', '').lower():
            return preview

        soup = BeautifulSoup(response.content, 'html.parser')

        og_title = soup.find("meta", property="og:title")
        twitter_title = soup.find("meta", attrs={"name": "twitter:title"})
        html_title = soup.title

        if og_title and og_title.get("content"):
            preview['title'] = og_title["content"]
        elif twitter_title and twitter_title.get("content"):
            preview['title'] = twitter_title["content"]
        elif html_title and html_title.string:
            preview['title'] = html_title.string.strip()

        og_description = soup.find("meta", property="og:description")
        twitter_description = soup.find("meta", attrs={"name": "twitter:description"})
        meta_description = soup.find("meta", attrs={"name": "description"})

        if og_description and og_description.get("content"):
            preview['description'] = og_description["content"]
        elif twitter_description and twitter_description.get("content"):
            preview['description'] = twitter_description["content"]
        elif meta_description and meta_description.get("content"):
            preview['description'] = meta_description["content"]

        og_image = soup.find("meta", property="og:image")
        twitter_image = soup.find("meta", attrs={"name": "twitter:image"})

        image_src = None
        if og_image and og_image.get("content"):
            image_src = og_image["content"]
        elif twitter_image and twitter_image.get("content"):
            image_src = twitter_image["content"]

        if image_src:
            preview['image_url'] = urllib.parse.urljoin(url, image_src)

        if preview['title'] and len(preview['title']) > 150:
            preview['title'] = preview['title'][:147] + "..."
        if preview['description'] and len(preview['description']) > 300:
            preview['description'] = preview['description'][:297] + "..."

        return preview

    except requests.exceptions.RequestException as e:
        print(f"Error al obtener la URL {url} para previsualización: {e}")
        return {'url': url, 'title': None, 'description': None, 'image_url': None}
    except Exception as e:
        print(f"Error inesperado al generar previsualización para {url}: {e}")
        return {'url': url, 'title': None, 'description': None, 'image_url': None}


# --- INICIALIZACIÓN Y PROCESADOR DE CONTEXTO ---
init_db()
regenerar_slugs_si_faltan()

@app.context_processor
def inject_global_vars():
    user_id = session.get('user_id')
    foto_usuario_actual = None
    num_notificaciones_no_leidas = 0
    num_mensajes_no_leidos = 0

    display_username_default = _("Pionero")
    display_username = session.get('display_username', session.get('username_login', display_username_default))

    if user_id:
        with sqlite3.connect('users.db') as conn:
            c = conn.cursor()
            c.execute('SELECT photo FROM profiles WHERE user_id = ?', (user_id,))
            resultado_foto = c.fetchone()
            if resultado_foto:
                foto_usuario_actual = resultado_foto[0]

            c.execute('SELECT COUNT(*) FROM notificaciones WHERE user_id = ? AND leida = 0', (user_id,))
            res_notif_count = c.fetchone()
            num_notificaciones_no_leidas = res_notif_count[0] if res_notif_count else 0

            excluded_ids = get_blocked_and_blocking_ids(user_id, c)
            params = [user_id, user_id]
            not_in_clause = ""
            if excluded_ids:
                not_in_clause = f" AND m.sender_id NOT IN ({','.join('?' for _ in excluded_ids)}) "
                params.extend(list(excluded_ids))

            c.execute(f'''SELECT COUNT(m.id) FROM messages m
                          JOIN conversation_participants cp ON m.conversation_id = cp.conversation_id
                          WHERE cp.user_id = ? AND m.sender_id != ? AND m.is_read = 0 {not_in_clause}''', tuple(params))
            res_msg_count = c.fetchone()
            num_mensajes_no_leidos = res_msg_count[0] if res_msg_count else 0

    return dict(
        foto_usuario_actual=foto_usuario_actual,
        notificaciones_no_leidas_count=num_notificaciones_no_leidas,
        unread_messages_count=num_mensajes_no_leidos,
        display_username_session=display_username,
        now=datetime.utcnow,
        available_languages=app.config['LANGUAGES'],
        current_locale=str(get_babel_locale())
    )
    
# --- RUTAS ---
@app.route('/language/<lang>')
def set_language(lang=None):
    if lang and lang in app.config['LANGUAGES']:
        session['language'] = lang
    else:
        flash(_('Idioma no soportado.'), 'warning')

    next_url = request.args.get('next') or request.referrer or url_for('index')
    return redirect(next_url)

@app.route('/')
def index():
    user_id = session.get('user_id')
    perfil_esta_completo_actual = check_profile_completion(user_id) if user_id else False
    return render_template('index.html', perfil_completo=perfil_esta_completo_actual)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username_login = request.form['username'].strip()
        password = request.form['password']
        if not username_login or not password:
            flash(_('El nombre de usuario y la contraseña son obligatorios.'), 'danger')
            return render_template('register.html')
        hashed_password = generate_password_hash(password)
        with sqlite3.connect('users.db') as conn:
            c = conn.cursor()
            try:
                c.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username_login, hashed_password))
                user_id_nuevo = c.lastrowid
                c.execute('INSERT INTO profiles (user_id, username, slug) VALUES (?, ?, ?)', (user_id_nuevo, None, None))
                conn.commit()
                flash(_('¡Registro exitoso! Ahora puedes iniciar sesión y completar tu perfil público.'), 'success')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash(_('Ese nombre de usuario para login ya existe. Por favor, elige otro.'), 'danger')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username_login = request.form['username']
        password = request.form['password']
        with sqlite3.connect('users.db') as conn:
            c = conn.cursor()
            c.execute('SELECT id, password FROM users WHERE username = ?', (username_login,))
            user_data = c.fetchone()
        if user_data and check_password_hash(user_data[1], password):
            session['user_id'] = user_data[0]
            session['username_login'] = username_login
            with sqlite3.connect('users.db') as conn:
                c_profile = conn.cursor()
                c_profile.execute('SELECT username FROM profiles WHERE user_id = ?', (user_data[0],))
                profile_data = c_profile.fetchone()
                session['display_username'] = profile_data[0] if profile_data and profile_data[0] and profile_data[0].strip() else username_login
            if not check_profile_completion(user_data[0]):
                 flash(_('¡Bienvenido! Por favor, completa tu perfil público para continuar.'), 'info')
                 return redirect(url_for('profile'))
            flash(_('Inicio de sesión exitoso.'), 'success')
            return redirect(url_for('feed'))
        else:
            flash(_("Credenciales inválidas. Inténtalo de nuevo."), 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash(_('Has cerrado sesión.'), 'info')
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id_actual = session['user_id']
    if request.method == 'POST':
        nuevo_username_perfil = request.form['username'].strip()
        nueva_bio = request.form['bio'].strip()
        archivo_foto = request.files.get('photo')
        if not nuevo_username_perfil or not nuevo_username_perfil.strip():
            flash(_('El nombre de usuario público no puede estar vacío.'), 'danger')
        else:
            nuevo_slug = generar_slug(nuevo_username_perfil)
            if not nuevo_slug:
                flash(_('El nombre de usuario público debe contener caracteres alfanuméricos válidos.'), 'danger')
            else:
                foto_actual_filename = None
                with sqlite3.connect('users.db') as conn:
                    c = conn.cursor()
                    c.execute('SELECT user_id FROM profiles WHERE (lower(username) = lower(?) OR lower(slug) = lower(?)) AND user_id != ?',
                              (nuevo_username_perfil, nuevo_slug, user_id_actual))
                    if c.fetchone():
                        flash(_("Ese nombre de usuario público o slug ya está en uso por otra persona."), 'danger')
                    else:
                        if archivo_foto and allowed_file(archivo_foto.filename):
                            nombre_seguro_foto = secure_filename(archivo_foto.filename)
                            foto_actual_filename = f"user_{user_id_actual}_{nombre_seguro_foto}"
                            try:
                                archivo_foto.save(os.path.join(app.config['UPLOAD_FOLDER'], foto_actual_filename))
                            except Exception as e:
                                flash(_("Error al guardar la foto: %(error)s", error=str(e)), "danger")
                                c.execute('SELECT photo FROM profiles WHERE user_id = ?', (user_id_actual,))
                                res_foto = c.fetchone(); foto_actual_filename = res_foto[0] if res_foto else None
                        else:
                            c.execute('SELECT photo FROM profiles WHERE user_id = ?', (user_id_actual,))
                            res_foto = c.fetchone(); foto_actual_filename = res_foto[0] if res_foto else None
                        c.execute('REPLACE INTO profiles (user_id, username, bio, photo, slug) VALUES (?, ?, ?, ?, ?)',
                                  (user_id_actual, nuevo_username_perfil, nueva_bio, foto_actual_filename, nuevo_slug))
                        conn.commit()
                        session['display_username'] = nuevo_username_perfil
                        flash(_("Perfil actualizado correctamente."), 'success')
                        return redirect(url_for('profile'))
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('SELECT username, bio, photo, slug FROM profiles WHERE user_id = ?', (user_id_actual,))
        datos_perfil = c.fetchone()
        if request.method == 'GET' and not (datos_perfil and datos_perfil[0] and datos_perfil[0].strip()):
            flash(_("Por favor, completa tu nombre de usuario público en el perfil."), "info")
    return render_template('profile.html', profile=datos_perfil)


@app.route('/feed')
def feed():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Debes completar tu perfil para ver el feed y publicar.'), 'warning')
        return redirect(url_for('profile'))

    feed_items = []
    default_username_display = _("Usuario")
    all_sections = []

    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute("SELECT id, name, slug FROM sections ORDER BY name ASC")
        sections_raw = c.fetchall()
        for section_row in sections_raw:
            all_sections.append(dict(section_row))

        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
        params = []
        excluded_placeholders = ""
        if excluded_ids:
            excluded_placeholders = ", ".join("?" for _ in excluded_ids)
            params.extend(list(excluded_ids))

        # 1. OBTENER PUBLICACIONES ORIGINALES (MODIFICADO para incluir previsualización)
        query_original_posts = f'''
            SELECT p.id, p.user_id AS autor_id_post, pr.username, pr.photo, p.content,
                   p.image_filename, p.timestamp AS post_timestamp_str, pr.slug,
                   s.name AS section_name, s.slug AS section_slug,
                   p.preview_url, p.preview_title, p.preview_description, p.preview_image_url
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN profiles pr ON u.id = pr.user_id
            LEFT JOIN sections s ON p.section_id = s.id
            { "WHERE p.user_id NOT IN (" + excluded_placeholders + ")" if excluded_ids else "" }
            ORDER BY p.timestamp DESC
            LIMIT 50
        '''
        c.execute(query_original_posts, tuple(params))
        original_posts_raw = c.fetchall()

        for post_data in original_posts_raw:
            post_id = post_data['id']
            post_timestamp_obj = parse_timestamp(post_data['post_timestamp_str'])
            # ... (lógica de reacciones, conteo de compartidos, comentarios - sin cambios)
            c.execute("SELECT reaction_type FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id_actual))
            reaccion_usuario_actual_row = c.fetchone()
            usuario_reacciono_info = {'reaction_type': reaccion_usuario_actual_row['reaction_type']} if reaccion_usuario_actual_row else None
            c.execute("SELECT COUNT(id) AS total_reactions FROM post_reactions WHERE post_id = ?", (post_id,))
            num_reacciones_totales_row = c.fetchone()
            num_reacciones_totales = num_reacciones_totales_row['total_reactions'] if num_reacciones_totales_row else 0
            c.execute("SELECT COUNT(id) FROM shared_posts WHERE original_post_id = ?", (post_id,))
            share_count_row = c.fetchone()
            share_count = share_count_row[0] if share_count_row else 0
            c.execute('''
                SELECT cm.id, pr_com.username AS comment_username, pr_com.photo AS comment_photo,
                       cm.content AS comment_content, cm.timestamp AS comment_timestamp_str,
                       pr_com.slug AS comment_slug, cm.parent_comment_id, cm.user_id AS comment_user_id
                FROM comments cm
                JOIN users u_com ON cm.user_id = u_com.id LEFT JOIN profiles pr_com ON u_com.id = pr_com.user_id
                WHERE cm.post_id = ? ORDER BY cm.timestamp ASC
            ''', (post_id,))
            comentarios_raw = c.fetchall()
            comments_map = {}
            structured_comments = []
            for row_com in comentarios_raw:
                comment_id = row_com['id']
                comment_timestamp_obj = parse_timestamp(row_com['comment_timestamp_str'])
                c.execute("SELECT COUNT(id) FROM comment_reactions WHERE comment_id = ?", (comment_id,))
                total_comment_reactions_row = c.fetchone()
                total_comment_reactions = total_comment_reactions_row[0] if total_comment_reactions_row else 0
                c.execute("SELECT reaction_type FROM comment_reactions WHERE comment_id = ? AND user_id = ?", (comment_id, user_id_actual))
                user_cr_row = c.fetchone()
                user_comment_reaction = {'reaction_type': user_cr_row['reaction_type']} if user_cr_row else None
                comments_map[comment_id] = {
                    'id': comment_id, 'username': (row_com['comment_username'] if row_com['comment_username'] and row_com['comment_username'].strip() else default_username_display),
                    'photo': row_com['comment_photo'], 'content': procesar_menciones_para_mostrar(row_com['comment_content']),
                    'timestamp': comment_timestamp_obj, 'slug': (row_com['comment_slug'] if row_com['comment_slug'] and row_com['comment_slug'].strip() else "#"),
                    'parent_comment_id': row_com['parent_comment_id'], 'user_id': row_com['comment_user_id'],
                    'replies': [], 'total_reactions': total_comment_reactions, 'user_reaction': user_comment_reaction
                }
            for cid, cdata in comments_map.items():
                if cdata['parent_comment_id'] and cdata['parent_comment_id'] in comments_map:
                    comments_map[cdata['parent_comment_id']]['replies'].append(cdata)
                else: structured_comments.append(cdata)

            feed_items.append({
                'item_type': 'original_post',
                'activity_timestamp': post_timestamp_obj,
                'id': post_id,
                'autor_id_post': post_data['autor_id_post'],
                'username': (post_data['username'] if post_data['username'] and post_data['username'].strip() else default_username_display),
                'photo': post_data['photo'],
                'slug': (post_data['slug'] if post_data['slug'] and post_data['slug'].strip() else "#"),
                'content': procesar_menciones_para_mostrar(post_data['content']),
                'image_filename': post_data['image_filename'],
                'timestamp': post_timestamp_obj,
                'comments': structured_comments,
                'total_reactions': num_reacciones_totales,
                'user_reaction': usuario_reacciono_info,
                'share_count': share_count,
                'section_name': post_data['section_name'],
                'section_slug': post_data['section_slug'],
                'preview_url': post_data['preview_url'],
                'preview_title': post_data['preview_title'],
                'preview_description': post_data['preview_description'],
                'preview_image_url': post_data['preview_image_url']
            })

        # 2. OBTENER PUBLICACIONES COMPARTIDAS (REPOSTS) (MODIFICADO para previsualización)
        params_shared = []
        where_clauses_shared = []
        if excluded_ids:
            where_clauses_shared.append(f"sp.user_id NOT IN ({excluded_placeholders})")
            params_shared.extend(list(excluded_ids))
            where_clauses_shared.append(f"op.user_id NOT IN ({excluded_placeholders})")
            params_shared.extend(list(excluded_ids))

        query_shared_posts = f'''
            SELECT
                sp.id AS share_id, sp.timestamp AS share_timestamp_str, sp.user_id AS sharer_user_id,
                sharer_profile.username AS sharer_username, sharer_profile.photo AS sharer_photo,
                sharer_profile.slug AS sharer_slug,
                sp.quote_content,
                op.id AS original_post_id, op.user_id AS original_author_user_id,
                op.content AS original_content, op.image_filename AS original_image_filename,
                op.timestamp AS original_timestamp_str,
                original_author_profile.username AS original_author_username,
                original_author_profile.photo AS original_author_photo,
                original_author_profile.slug AS original_author_slug,
                s_orig.name AS original_section_name, s_orig.slug AS original_section_slug,
                op.preview_url, op.preview_title, op.preview_description, op.preview_image_url
            FROM shared_posts sp
            JOIN users sharer_user ON sp.user_id = sharer_user.id
            LEFT JOIN profiles sharer_profile ON sp.user_id = sharer_profile.user_id
            JOIN posts op ON sp.original_post_id = op.id
            LEFT JOIN sections s_orig ON op.section_id = s_orig.id
            JOIN users original_author_user ON op.user_id = original_author_user.id
            LEFT JOIN profiles original_author_profile ON op.user_id = original_author_profile.user_id
            { "WHERE " + " AND ".join(where_clauses_shared) if where_clauses_shared else "" }
            ORDER BY sp.timestamp DESC
            LIMIT 50
        '''
        c.execute(query_shared_posts, tuple(params_shared))
        shared_posts_raw = c.fetchall()

        for shared_data in shared_posts_raw:
            original_post_id = shared_data['original_post_id']
            share_timestamp_obj = parse_timestamp(shared_data['share_timestamp_str'])
            original_timestamp_obj = parse_timestamp(shared_data['original_timestamp_str'])
            # ... (lógica de reacciones, conteo de compartidos, comentarios para el post original - sin cambios) ...
            c.execute("SELECT reaction_type FROM post_reactions WHERE post_id = ? AND user_id = ?", (original_post_id, user_id_actual))
            reaccion_usuario_original_row = c.fetchone()
            usuario_reacciono_original_info = {'reaction_type': reaccion_usuario_original_row['reaction_type']} if reaccion_usuario_original_row else None
            c.execute("SELECT COUNT(id) AS total_reactions FROM post_reactions WHERE post_id = ?", (original_post_id,))
            num_reacciones_original_totales_row = c.fetchone()
            num_reacciones_original_totales = num_reacciones_original_totales_row['total_reactions'] if num_reacciones_original_totales_row else 0
            c.execute("SELECT COUNT(id) FROM shared_posts WHERE original_post_id = ?", (original_post_id,))
            share_count_original_row = c.fetchone()
            share_count_original = share_count_original_row[0] if share_count_original_row else 0
            c.execute('''
                SELECT cm.id, pr_com.username AS comment_username, pr_com.photo AS comment_photo,
                       cm.content AS comment_content, cm.timestamp AS comment_timestamp_str,
                       pr_com.slug AS comment_slug, cm.parent_comment_id, cm.user_id AS comment_user_id
                FROM comments cm
                JOIN users u_com ON cm.user_id = u_com.id LEFT JOIN profiles pr_com ON u_com.id = pr_com.user_id
                WHERE cm.post_id = ? ORDER BY cm.timestamp ASC
            ''', (original_post_id,))
            comentarios_original_raw = c.fetchall()
            comments_original_map = {}
            structured_original_comments = []
            for row_com_orig in comentarios_original_raw:
                comment_id_orig = row_com_orig['id']
                comment_original_timestamp_obj = parse_timestamp(row_com_orig['comment_timestamp_str'])
                c.execute("SELECT COUNT(id) FROM comment_reactions WHERE comment_id = ?", (comment_id_orig,))
                total_comment_reactions_o_row = c.fetchone()
                total_comment_reactions_o = total_comment_reactions_o_row[0] if total_comment_reactions_o_row else 0
                c.execute("SELECT reaction_type FROM comment_reactions WHERE comment_id = ? AND user_id = ?", (comment_id_orig, user_id_actual))
                user_cr_o_row = c.fetchone()
                user_comment_reaction_o = {'reaction_type': user_cr_o_row['reaction_type']} if user_cr_o_row else None
                comments_original_map[comment_id_orig] = {
                    'id': comment_id_orig, 'username': (row_com_orig['comment_username'] if row_com_orig['comment_username'] and row_com_orig['comment_username'].strip() else default_username_display),
                    'photo': row_com_orig['comment_photo'], 'content': procesar_menciones_para_mostrar(row_com_orig['comment_content']),
                    'timestamp': comment_original_timestamp_obj, 'slug': (row_com_orig['comment_slug'] if row_com_orig['comment_slug'] and row_com_orig['comment_slug'].strip() else "#"),
                    'parent_comment_id': row_com_orig['parent_comment_id'], 'user_id': row_com_orig['comment_user_id'],
                    'replies': [], 'total_reactions': total_comment_reactions_o, 'user_reaction': user_comment_reaction_o
                }
            for cid_orig, cdata_orig in comments_original_map.items():
                if cdata_orig['parent_comment_id'] and cdata_orig['parent_comment_id'] in comments_original_map:
                    comments_original_map[cdata_orig['parent_comment_id']]['replies'].append(cdata_orig)
                else: structured_original_comments.append(cdata_orig)

            feed_items.append({
                'item_type': 'shared_post',
                'activity_timestamp': share_timestamp_obj,
                'share_id': shared_data['share_id'],
                'sharer_user_id': shared_data['sharer_user_id'],
                'sharer_username': (shared_data['sharer_username'] if shared_data['sharer_username'] and shared_data['sharer_username'].strip() else default_username_display),
                'sharer_photo': shared_data['sharer_photo'],
                'sharer_slug': (shared_data['sharer_slug'] if shared_data['sharer_slug'] and shared_data['sharer_slug'].strip() else "#"),
                'share_timestamp': share_timestamp_obj,
                'quote_content': shared_data['quote_content'],
                'original_post': {
                    'id': original_post_id,
                    'autor_id_post': shared_data['original_author_user_id'],
                    'username': (shared_data['original_author_username'] if shared_data['original_author_username'] and shared_data['original_author_username'].strip() else default_username_display),
                    'photo': shared_data['original_author_photo'],
                    'slug': (shared_data['original_author_slug'] if shared_data['original_author_slug'] and shared_data['original_author_slug'].strip() else "#"),
                    'content': procesar_menciones_para_mostrar(shared_data['original_content']),
                    'image_filename': shared_data['original_image_filename'],
                    'timestamp': original_timestamp_obj,
                    'comments': structured_original_comments,
                    'total_reactions': num_reacciones_original_totales,
                    'user_reaction': usuario_reacciono_original_info,
                    'share_count': share_count_original,
                    'section_name': shared_data['original_section_name'],
                    'section_slug': shared_data['original_section_slug'],
                    'preview_url': shared_data['preview_url'],
                    'preview_title': shared_data['preview_title'],
                    'preview_description': shared_data['preview_description'],
                    'preview_image_url': shared_data['preview_image_url']
                }
            })
        feed_items.sort(key=lambda item: item['activity_timestamp'] if item['activity_timestamp'] else datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return render_template('feed.html', posts=feed_items, sections=all_sections)


@app.route('/post', methods=['POST'])
def post():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil antes de publicar.'), 'warning')
        return redirect(url_for('profile'))

    contenido_post = request.form['content']
    archivo_imagen = request.files.get('post_image')
    section_id_str = request.form.get('section_id')
    section_id = None
    if section_id_str and section_id_str.isdigit():
        section_id = int(section_id_str)

    contenido_post_limpio = contenido_post.strip()

    if not contenido_post_limpio and not archivo_imagen:
        flash(_('La publicación no puede estar completamente vacía. Añade texto o una imagen.'), 'danger')
        return redirect(url_for('feed'))

    nombre_archivo_imagen = None
    if archivo_imagen and archivo_imagen.filename != '':
        if allowed_file(archivo_imagen.filename):
            timestamp_actual_img = datetime.now().strftime("%Y%m%d%H%M%S%f")
            nombre_seguro_original = secure_filename(archivo_imagen.filename)
            nombre_archivo_imagen = f"post_{user_id_actual}_{timestamp_actual_img}_{nombre_seguro_original}"
            ruta_guardado = os.path.join(POST_IMAGES_FOLDER, nombre_archivo_imagen)
            try:
                archivo_imagen.save(ruta_guardado)
            except Exception as e:
                flash(_("Error al guardar la imagen de la publicación: %(error)s", error=str(e)), "danger")
                nombre_archivo_imagen = None
        else:
            flash(_('Tipo de archivo de imagen no permitido. Permitidos: png, jpg, jpeg, gif.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

    if not contenido_post_limpio and not nombre_archivo_imagen:
         flash(_('La publicación no puede estar completamente vacía. Añade texto o una imagen.'), 'danger')
         return redirect(request.referrer or url_for('feed'))


    # --- INICIO: Lógica para Previsualización de Enlaces ---
    preview_url_db = None
    preview_title_db = None
    preview_description_db = None
    preview_image_url_db = None

    first_url_found = extract_first_url(contenido_post)

    if first_url_found:
        link_preview_data = generate_link_preview(first_url_found)
        if link_preview_data:
            if link_preview_data.get('title') or link_preview_data.get('description'):
                preview_url_db = link_preview_data.get('url')
                preview_title_db = link_preview_data.get('title')
                preview_description_db = link_preview_data.get('description')
                preview_image_url_db = link_preview_data.get('image_url')
    # --- FIN: Lógica para Previsualización de Enlaces ---

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO posts (user_id, content, image_filename, section_id,
                               preview_url, preview_title, preview_description, preview_image_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id_actual, contenido_post, nombre_archivo_imagen, section_id,
              preview_url_db, preview_title_db, preview_description_db, preview_image_url_db))
        id_nuevo_post = c.lastrowid
        conn.commit()

    if id_nuevo_post and contenido_post_limpio:
        procesar_menciones_y_notificar(contenido_post_limpio, user_id_actual, id_nuevo_post, "publicación")

    flash(_('Publicación creada.'), 'success')
    return redirect(request.referrer or url_for('feed'))

@app.route('/post/<int:post_id>/delete', methods=['POST'])
def delete_post(post_id):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para eliminar publicaciones.'), 'warning')
        return redirect(url_for('login'))

    user_id_actual = session['user_id']

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute("SELECT user_id, image_filename FROM posts WHERE id = ?", (post_id,))
        post_data = c.fetchone()

        if not post_data:
            flash(_('Publicación no encontrada.'), 'danger')
            return redirect(url_for('feed'))

        autor_id_post = post_data[0]
        image_to_delete = post_data[1]

        if autor_id_post != user_id_actual:
            flash(_('No tienes permiso para eliminar esta publicación.'), 'danger')
            return redirect(url_for('feed'))

        if image_to_delete:
            try:
                ruta_imagen = os.path.join(POST_IMAGES_FOLDER, image_to_delete)
                if os.path.exists(ruta_imagen):
                    os.remove(ruta_imagen)
            except Exception as e:
                print(f"Error al eliminar el archivo de imagen {image_to_delete}: {e}")

        c.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        conn.commit()
        flash(_('Publicación eliminada correctamente.'), 'success')

    return redirect(url_for('feed'))


@app.route('/react_to_post/<int:post_id>', methods=['POST'])
def react_to_post(post_id):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para reaccionar.'), 'warning')
        return redirect(url_for('login'))

    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil antes de reaccionar.'), 'warning')
        return redirect(url_for('profile'))

    reaction_type = request.form.get('reaction_type')
    allowed_reactions = ['like', 'love', 'haha', 'wow', 'sad', 'angry']
    if not reaction_type or reaction_type not in allowed_reactions:
        flash(_('Tipo de reacción no válido.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()

        c.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,))
        post_info = c.fetchone()
        if not post_info:
            flash(_('Publicación no encontrada.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

        autor_post_id = post_info[0]
        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
        if autor_post_id in excluded_ids:
            flash(_('No puedes interactuar con este usuario o publicación.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

        c.execute("SELECT id, reaction_type FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id_actual))
        existing_reaction = c.fetchone()

        if existing_reaction:
            existing_reaction_id = existing_reaction[0]
            existing_reaction_type = existing_reaction[1]

            if existing_reaction_type == reaction_type:
                c.execute("DELETE FROM post_reactions WHERE id = ?", (existing_reaction_id,))
                flash(_('Reacción eliminada.'), 'info')
            else:
                c.execute("UPDATE post_reactions SET reaction_type = ?, timestamp = ? WHERE id = ?",
                          (reaction_type, datetime.utcnow(), existing_reaction_id))
                flash(_('Reacción actualizada.'), 'success')
        else:
            c.execute("INSERT INTO post_reactions (post_id, user_id, reaction_type, timestamp) VALUES (?, ?, ?, ?)",
                      (post_id, user_id_actual, reaction_type, datetime.utcnow()))
            flash(_('Has reaccionado a la publicación.'), 'success')

            if autor_post_id != user_id_actual:
                c.execute('SELECT slug, username FROM profiles WHERE user_id = ?', (user_id_actual,))
                reactor_perfil = c.fetchone()
                reactor_slug = reactor_perfil[0] if reactor_perfil and reactor_perfil[0] else "#"
                reactor_nombre = reactor_perfil[1] if reactor_perfil and reactor_perfil[1] else _("Usuario")

                reactor_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=reactor_slug)}">@{reactor_nombre}</a>'
                post_link_text = _("publicación")
                post_link_html = f'<a href="{url_for("ver_publicacion_individual", post_id_vista=post_id)}">{post_link_text}</a>'
                mensaje_template = _('%(reactor_link)s reaccionó a tu %(post_link)s.')
                mensaje_notif = mensaje_template % {'reactor_link': reactor_link_html, 'post_link': post_link_html}

                c.execute('INSERT INTO notificaciones (user_id, mensaje, tipo, referencia_id) VALUES (?, ?, ?, ?)',
                          (autor_post_id, mensaje_notif, f'reaccion_{reaction_type}', post_id))
        conn.commit()
    return redirect(request.referrer or url_for('feed'))


@app.route('/comment/<int:post_id>', methods=['POST'])
def comment(post_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil antes de comentar.'), 'warning')
        return redirect(url_for('profile'))

    contenido_comentario = request.form['content'].strip()
    parent_comment_id_str = request.form.get('parent_comment_id')
    parent_comment_id = None
    if parent_comment_id_str:
        try:
            parent_comment_id = int(parent_comment_id_str)
        except (ValueError, TypeError):
            parent_comment_id = None
            flash(_('ID de comentario padre inválido.'), 'warning')
            return redirect(request.referrer or url_for('feed'))

    if not contenido_comentario:
        flash(_('El comentario no puede estar vacío.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()

        c.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,))
        post_info = c.fetchone()
        if not post_info:
            flash(_('La publicación a la que intentas responder no existe.'), 'danger')
            return redirect(url_for('feed'))

        autor_post_id = post_info[0]
        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
        if autor_post_id in excluded_ids:
            flash(_('No puedes interactuar con este usuario o publicación.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

        if parent_comment_id:
            c.execute("SELECT user_id FROM comments WHERE id = ?", (parent_comment_id,))
            parent_comment_info = c.fetchone()
            if not parent_comment_info:
                flash(_('El comentario al que intentas responder no existe.'), 'danger')
                return redirect(request.referrer or url_for('feed'))

            autor_parent_comment_id = parent_comment_info[0]
            if autor_parent_comment_id in excluded_ids:
                flash(_('No puedes interactuar con este usuario o comentario.'), 'danger')
                return redirect(request.referrer or url_for('feed'))

        try:
            c.execute('INSERT INTO comments (post_id, user_id, content, parent_comment_id) VALUES (?, ?, ?, ?)',
                      (post_id, user_id_actual, contenido_comentario, parent_comment_id))
            id_nuevo_comentario = c.lastrowid
            conn.commit()

            if id_nuevo_comentario:
                procesar_menciones_y_notificar(contenido_comentario, user_id_actual, post_id, "comentario")

                if parent_comment_id:
                    c.execute("SELECT user_id FROM comments WHERE id = ?", (parent_comment_id,))
                    autor_comentario_padre_row = c.fetchone()
                    id_autor_comentario_padre = autor_comentario_padre_row[0]
                    if autor_comentario_padre_row and id_autor_comentario_padre != user_id_actual and id_autor_comentario_padre not in excluded_ids:
                        c.execute("SELECT slug, username FROM profiles WHERE user_id = ?", (user_id_actual,))
                        replier_profile = c.fetchone()
                        replier_slug = replier_profile[0] if replier_profile and replier_profile[0] else "#"
                        replier_name = replier_profile[1] if replier_profile and replier_profile[1] else _("Usuario")

                        replier_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=replier_slug)}">@{replier_name}</a>'
                        post_link_html = _('una <a href="%(post_url)s">publicación</a>') % {'post_url': url_for("ver_publicacion_individual", post_id_vista=post_id)}

                        mensaje_template = _('%(replier_link)s ha respondido a tu comentario en %(post_link)s.')
                        mensaje_notif_respuesta = mensaje_template % {'replier_link': replier_link_html, 'post_link': post_link_html}

                        c.execute('INSERT INTO notificaciones (user_id, mensaje, tipo, referencia_id) VALUES (?, ?, ?, ?)',
                                  (id_autor_comentario_padre, mensaje_notif_respuesta, 'respuesta_comentario', post_id))
                        conn.commit()
            flash(_('Comentario añadido.'), 'success')
        except sqlite3.Error as e:
            flash(_('Error al guardar el comentario: %(error)s', error=str(e)), 'danger')
            conn.rollback()

    if request.referrer and f'/post/{post_id}' in request.referrer:
        return redirect(url_for('ver_publicacion_individual', post_id_vista=post_id))
    return redirect(request.referrer or url_for('feed'))


@app.route('/comment/<int:comment_id>/delete', methods=['POST'])
def delete_comment(comment_id):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para eliminar comentarios.'), 'warning')
        return redirect(url_for('login'))

    user_id_actual = session['user_id']

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute("SELECT user_id, post_id FROM comments WHERE id = ?", (comment_id,))
        comment_data = c.fetchone()

        if not comment_data:
            flash(_('Comentario no encontrado.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

        autor_id_comment = comment_data[0]
        post_id_original = comment_data[1]

        if autor_id_comment != user_id_actual:
            flash(_('No tienes permiso para eliminar este comentario.'), 'danger')
            if post_id_original:
                 return redirect(url_for('ver_publicacion_individual', post_id_vista=post_id_original))
            return redirect(url_for('feed'))

        c.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        conn.commit()
        flash(_('Comentario eliminado correctamente.'), 'success')

    if post_id_original:
        return redirect(url_for('ver_publicacion_individual', post_id_vista=post_id_original))
    else:
        return redirect(url_for('feed'))


@app.route('/react_to_comment/<int:comment_id>', methods=['POST'])
def react_to_comment(comment_id):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para reaccionar a los comentarios.'), 'warning')
        return redirect(url_for('login'))

    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil antes de reaccionar.'), 'warning')
        return redirect(url_for('profile'))

    reaction_type = request.form.get('reaction_type')
    allowed_reactions = ['like', 'love', 'haha', 'wow', 'sad', 'angry']
    if not reaction_type or reaction_type not in allowed_reactions:
        flash(_('Tipo de reacción no válido.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()

        c.execute("SELECT user_id, post_id FROM comments WHERE id = ?", (comment_id,))
        comment_info = c.fetchone()
        if not comment_info:
            flash(_('Comentario no encontrado.'), 'danger')
            return redirect(url_for('feed'))

        autor_comment_id = comment_info[0]
        post_id_original = comment_info[1]

        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
        if autor_comment_id in excluded_ids:
            flash(_('No puedes interactuar con este usuario o comentario.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

        c.execute("SELECT id, reaction_type FROM comment_reactions WHERE comment_id = ? AND user_id = ?",
                  (comment_id, user_id_actual))
        existing_reaction = c.fetchone()

        if existing_reaction:
            existing_reaction_id = existing_reaction[0]
            existing_reaction_type = existing_reaction[1]

            if existing_reaction_type == reaction_type:
                c.execute("DELETE FROM comment_reactions WHERE id = ?", (existing_reaction_id,))
                flash(_('Reacción al comentario eliminada.'), 'info')
            else:
                c.execute("UPDATE comment_reactions SET reaction_type = ?, timestamp = ? WHERE id = ?",
                          (reaction_type, datetime.utcnow(), existing_reaction_id))
                flash(_('Reacción al comentario actualizada.'), 'success')
        else:
            c.execute("INSERT INTO comment_reactions (comment_id, user_id, reaction_type, timestamp) VALUES (?, ?, ?, ?)",
                      (comment_id, user_id_actual, reaction_type, datetime.utcnow()))
            flash(_('Has reaccionado al comentario.'), 'success')

            if autor_comment_id != user_id_actual:
                c.execute('SELECT slug, username FROM profiles WHERE user_id = ?', (user_id_actual,))
                reactor_perfil = c.fetchone()
                reactor_slug = reactor_perfil[0] if reactor_perfil and reactor_perfil[0] else "#"
                reactor_nombre = reactor_perfil[1] if reactor_perfil and reactor_perfil[1] else _("Usuario")

                reactor_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=reactor_slug)}">@{reactor_nombre}</a>'
                comment_link_html = f'<a href="{url_for("ver_publicacion_individual", post_id_vista=post_id_original)}#comment-{comment_id}">{_("comentario")}</a>'
                mensaje_template = _('%(reactor_link)s reaccionó a tu %(comment_link_html)s.')

                mensaje_notif = mensaje_template % {'reactor_link': reactor_link_html, 'comment_link_html': comment_link_html}

                c.execute('INSERT INTO notificaciones (user_id, mensaje, tipo, referencia_id) VALUES (?, ?, ?, ?)',
                          (autor_comment_id, mensaje_notif, f'reaccion_comentario_{reaction_type}', comment_id))

        conn.commit()

    redirect_url = (request.referrer or url_for('feed')).split('#')[0]
    final_url = f"{redirect_url}#comment-{comment_id}"
    return redirect(final_url)


@app.route('/post/<int:post_id>/share', methods=['POST'])
def share_post(post_id):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para compartir publicaciones.'), 'warning')
        return redirect(url_for('login'))

    user_id_actual = session['user_id']

    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil antes de compartir publicaciones.'), 'warning')
        return redirect(url_for('profile'))

    quote_content = request.form.get('quote_content', '').strip()

    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,))
        post_original_data = c.fetchone()

        if not post_original_data:
            flash(_('La publicación que intentas compartir no existe.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

        id_autor_original = post_original_data['user_id']

        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
        if id_autor_original in excluded_ids:
            flash(_('No puedes interactuar con esta publicación.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

        try:
            c.execute('''
                INSERT INTO shared_posts (user_id, original_post_id, quote_content, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (user_id_actual, post_id, quote_content if quote_content else None, datetime.utcnow()))

            insert_rowcount = c.rowcount
            id_nuevo_share = c.lastrowid

            if insert_rowcount > 0 and id_autor_original != user_id_actual:
                c.execute("SELECT username, slug FROM profiles WHERE user_id = ?", (user_id_actual,))
                sharer_profile_data = c.fetchone()

                sharer_username = sharer_profile_data['username'] if sharer_profile_data and sharer_profile_data['username'] else _("Alguien")
                sharer_slug = sharer_profile_data['slug'] if sharer_profile_data and sharer_profile_data['slug'] else "#"

                sharer_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=sharer_slug)}">@{sharer_username}</a>'
                post_link_text = _("publicación")
                post_link_html = f'<a href="{url_for("ver_publicacion_individual", post_id_vista=post_id)}">{post_link_text}</a>'

                if quote_content:
                    mensaje_template = _('%(sharer_link)s ha citado tu %(post_link)s.')
                    tipo_notificacion = 'share_post_with_quote'
                else:
                    mensaje_template = _('%(sharer_link)s ha compartido tu %(post_link)s.')
                    tipo_notificacion = 'share_post'

                mensaje_notif = mensaje_template % {'sharer_link': sharer_link_html, 'post_link': post_link_html}

                c.execute('''
                    INSERT INTO notificaciones (user_id, mensaje, tipo, referencia_id)
                    VALUES (?, ?, ?, ?)
                ''', (id_autor_original, mensaje_notif, tipo_notificacion, post_id))

            conn.commit()
            if quote_content:
                flash(_('Publicación citada correctamente.'), 'success')
            else:
                flash(_('Publicación compartida correctamente.'), 'success')

        except sqlite3.IntegrityError:
            flash(_('Ya has compartido esta publicación anteriormente.'), 'info')

        except sqlite3.Error as e:
            flash(_('Ha ocurrido un error al intentar compartir la publicación: %(error)s', error=str(e)), 'danger')
            conn.rollback()

    return redirect(request.referrer or url_for('feed'))

@app.route('/enviar_solicitud/<int:id_receptor_solicitud>', methods=['POST'])
def enviar_solicitud(id_receptor_solicitud):
    if 'user_id' not in session: return redirect(url_for('login'))
    id_solicitante_actual = session['user_id']
    if not check_profile_completion(id_solicitante_actual):
        flash(_('Por favor, completa tu perfil antes de enviar solicitudes.'), 'warning')
        return redirect(request.referrer or url_for('profile'))

    if id_solicitante_actual == id_receptor_solicitud:
        flash(_('No puedes enviarte una solicitud a ti mismo.'), 'warning')
        return redirect(request.referrer or url_for('feed'))

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()

        excluded_ids = get_blocked_and_blocking_ids(id_solicitante_actual, c)
        if id_receptor_solicitud in excluded_ids:
            flash(_('No puedes interactuar con este usuario.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

        try:
            c.execute('SELECT id FROM contactos WHERE (solicitante_id = ? AND receptor_id = ?) OR (solicitante_id = ? AND receptor_id = ?)',
                      (id_solicitante_actual, id_receptor_solicitud, id_receptor_solicitud, id_solicitante_actual))
            if c.fetchone():
                flash(_('Ya existe una solicitud o conexión con este usuario.'), 'info')
                return redirect(request.referrer or url_for('feed'))

            c.execute("INSERT INTO contactos (solicitante_id, receptor_id, estado) VALUES (?, ?, 'pendiente')",
                      (id_solicitante_actual, id_receptor_solicitud))
            conn.commit()

            c.execute('SELECT slug, username FROM profiles WHERE user_id = ?', (id_solicitante_actual,))
            solicitante_perfil = c.fetchone()
            solicitante_slug = solicitante_perfil[0] if solicitante_perfil and solicitante_perfil[0] else "#"
            solicitante_nombre = solicitante_perfil[1] if solicitante_perfil and solicitante_perfil[1] else _("Usuario")

            solicitante_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=solicitante_slug)}">@{solicitante_nombre}</a>'
            mensaje_template = _('%(solicitante_link)s te ha enviado una solicitud de contacto.')
            mensaje_notif = mensaje_template % {'solicitante_link': solicitante_link_html}

            c.execute('INSERT INTO notificaciones (user_id, mensaje, tipo, referencia_id) VALUES (?, ?, ?, ?)',
                      (id_receptor_solicitud, mensaje_notif, 'solicitud_contacto', id_solicitante_actual))
            conn.commit()
            flash(_('Solicitud de contacto enviada.'), 'success')
        except sqlite3.IntegrityError:
            flash(_('Error al enviar la solicitud. Puede que ya exista una.'), 'warning')
    return redirect(request.referrer or url_for('feed'))


@app.route('/aceptar_solicitud/<int:id_solicitante>', methods=['POST'])
def aceptar_solicitud(id_solicitante):
    if 'user_id' not in session: return redirect(url_for('login'))
    id_receptor_actual = session['user_id']
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()

        excluded_ids = get_blocked_and_blocking_ids(id_receptor_actual, c)
        if id_solicitante in excluded_ids:
            flash(_('No puedes interactuar con este usuario.'), 'danger')
            return redirect(request.referrer or url_for('notificaciones'))

        c.execute("UPDATE contactos SET estado = 'aceptado' WHERE solicitante_id = ? AND receptor_id = ? AND estado = 'pendiente'",
                  (id_solicitante, id_receptor_actual))
        conn.commit()
        if c.rowcount > 0:
            flash(_('Solicitud de contacto aceptada.'), 'success')
            c.execute('SELECT slug, username FROM profiles WHERE user_id = ?', (id_receptor_actual,))
            receptor_perfil = c.fetchone()
            receptor_slug = receptor_perfil[0] if receptor_perfil and receptor_perfil[0] else "#"
            receptor_nombre = receptor_perfil[1] if receptor_perfil and receptor_perfil[1] else _("Usuario")

            receptor_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=receptor_slug)}">@{receptor_nombre}</a>'
            mensaje_template = _('%(receptor_link)s aceptó tu solicitud de contacto.')
            mensaje_notif = mensaje_template % {'receptor_link': receptor_link_html}

            c.execute('INSERT INTO notificaciones (user_id, mensaje, tipo, referencia_id) VALUES (?, ?, ?, ?)',
                      (id_solicitante, mensaje_notif, 'solicitud_aceptada', id_receptor_actual))
            conn.commit()
        else:
            flash(_('No se pudo aceptar la solicitud (quizás ya no estaba pendiente o no era para ti).'), 'warning')
    return redirect(request.referrer or url_for('notificaciones'))


@app.route('/rechazar_solicitud/<int:id_solicitante>', methods=['POST'])
def rechazar_solicitud(id_solicitante):
    if 'user_id' not in session: return redirect(url_for('login'))
    id_receptor_actual = session['user_id']
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute("DELETE FROM contactos WHERE solicitante_id = ? AND receptor_id = ? AND estado = 'pendiente'",
                  (id_solicitante, id_receptor_actual))
        conn.commit()
        if c.rowcount > 0:
            flash(_('Solicitud de contacto rechazada.'), 'info')
        else:
            flash(_('No se pudo rechazar la solicitud (quizás ya no estaba pendiente o no era para ti).'), 'warning')
    return redirect(request.referrer or url_for('notificaciones'))


@app.route('/eliminar_contacto/<int:id_otro_usuario>', methods=['POST'])
def eliminar_contacto(id_otro_usuario):
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id_actual = session['user_id']
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('DELETE FROM contactos WHERE (solicitante_id = ? AND receptor_id = ?) OR (solicitante_id = ? AND receptor_id = ?)',
                  (user_id_actual, id_otro_usuario, id_otro_usuario, user_id_actual))
        conn.commit()
        if c.rowcount > 0:
            flash(_('Contacto eliminado.'), 'success')
        else:
            flash(_('No se encontró una relación de contacto para eliminar.'), 'info')
    return redirect(request.referrer or url_for('feed'))


@app.route('/block_user/<int:user_to_block_id>', methods=['POST'])
def block_user(user_to_block_id):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para bloquear usuarios.'), 'warning')
        return redirect(url_for('login'))

    blocker_id = session['user_id']

    if blocker_id == user_to_block_id:
        flash(_('No te puedes bloquear a ti mismo.'), 'warning')
        return redirect(request.referrer or url_for('feed'))

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        try:
            c.execute("INSERT INTO blocked_users (blocker_user_id, blocked_user_id) VALUES (?, ?)",
                      (blocker_id, user_to_block_id))

            c.execute('''
                DELETE FROM contactos
                WHERE (solicitante_id = ? AND receptor_id = ?)
                   OR (solicitante_id = ? AND receptor_id = ?)
            ''', (blocker_id, user_to_block_id, user_to_block_id, blocker_id))

            conn.commit()
            flash(_('Usuario bloqueado correctamente. Ya no verás sus publicaciones ni podrás interactuar con él/ella.'), 'success')

        except sqlite3.IntegrityError:
            flash(_('Este usuario ya está en tu lista de bloqueados.'), 'info')

        except sqlite3.Error as e:
            conn.rollback()
            flash(_('Ha ocurrido un error al intentar bloquear al usuario: %(error)s', error=str(e)), 'danger')

    return redirect(request.referrer or url_for('feed'))


@app.route('/unblock_user/<int:user_to_unblock_id>', methods=['POST'])
def unblock_user(user_to_unblock_id):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para desbloquear usuarios.'), 'warning')
        return redirect(url_for('login'))

    blocker_id = session['user_id']

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        try:
            c.execute("DELETE FROM blocked_users WHERE blocker_user_id = ? AND blocked_user_id = ?",
                      (blocker_id, user_to_unblock_id))

            conn.commit()

            if c.rowcount > 0:
                flash(_('Usuario desbloqueado correctamente.'), 'success')
            else:
                flash(_('Este usuario no estaba en tu lista de bloqueados.'), 'info')

        except sqlite3.Error as e:
            conn.rollback()
            flash(_('Ha ocurrido un error al intentar desbloquear al usuario: %(error)s', error=str(e)), 'danger')

    return redirect(request.referrer or url_for('feed'))

@app.route('/ver_perfil/<slug_perfil>')
def ver_perfil(slug_perfil):
    if not slug_perfil or not slug_perfil.strip() or slug_perfil == "#":
        flash(_("No se puede acceder a un perfil sin un slug válido."), "danger")
        return redirect(url_for('feed'))

    user_id_visitante = session.get('user_id')
    default_username_display = _("Usuario")
    profile_items = []

    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute('SELECT id, user_id, username, bio, photo, slug FROM profiles WHERE lower(slug) = lower(?)', (slug_perfil,))
        perfil_visitado_data = c.fetchone()

        if not perfil_visitado_data:
            flash(_("Perfil no encontrado."), "danger")
            return redirect(url_for('feed'))

        id_usuario_dueño_perfil = perfil_visitado_data['user_id']

        if user_id_visitante:
            excluded_ids = get_blocked_and_blocking_ids(user_id_visitante, c)
            if id_usuario_dueño_perfil in excluded_ids:
                flash(_('No puedes ver el perfil de este usuario.'), 'danger')
                return redirect(url_for('feed'))

        nombre_display_dueño_perfil = perfil_visitado_data['username'] if perfil_visitado_data['username'] and perfil_visitado_data['username'].strip() else default_username_display
        bio_dueño_perfil = perfil_visitado_data['bio']
        foto_dueño_perfil = perfil_visitado_data['photo']
        slug_dueño_perfil = perfil_visitado_data['slug']

        # OBTENER PUBLICACIONES ORIGINALES (MODIFICADO para previsualización)
        c.execute('''
            SELECT p.id, p.user_id AS autor_id_post, p.content,
                   p.image_filename, p.timestamp AS post_timestamp_str,
                   s.name AS section_name, s.slug AS section_slug,
                   p.preview_url, p.preview_title, p.preview_description, p.preview_image_url
            FROM posts p
            LEFT JOIN sections s ON p.section_id = s.id
            WHERE p.user_id = ?
            ORDER BY p.timestamp DESC
        ''', (id_usuario_dueño_perfil,))
        original_posts_raw = c.fetchall()

        for post_data in original_posts_raw:
            post_id = post_data['id']
            original_post_timestamp_obj = parse_timestamp(post_data['post_timestamp_str'])
            user_reaction_on_post = None
            if user_id_visitante:
                c.execute("SELECT reaction_type FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id_visitante))
                reaccion_usuario_row = c.fetchone()
                if reaccion_usuario_row: user_reaction_on_post = {'reaction_type': reaccion_usuario_row['reaction_type']}
            c.execute("SELECT COUNT(id) AS total_reactions FROM post_reactions WHERE post_id = ?", (post_id,))
            num_reacciones_totales_row = c.fetchone()
            total_reactions_on_post = num_reacciones_totales_row['total_reactions'] if num_reacciones_totales_row else 0
            c.execute("SELECT COUNT(id) FROM shared_posts WHERE original_post_id = ?", (post_id,))
            share_count_row = c.fetchone()
            share_count = share_count_row[0] if share_count_row else 0

            profile_items.append({
                'item_type': 'original_post',
                'activity_timestamp': original_post_timestamp_obj,
                'id': post_id,
                'autor_id_post': id_usuario_dueño_perfil,
                'username': nombre_display_dueño_perfil,
                'photo': foto_dueño_perfil,
                'slug': slug_dueño_perfil,
                'content': procesar_menciones_para_mostrar(post_data['content']),
                'image_filename': post_data['image_filename'],
                'timestamp': original_post_timestamp_obj,
                'comments': [],
                'total_reactions': total_reactions_on_post,
                'user_reaction': user_reaction_on_post,
                'share_count': share_count,
                'section_name': post_data['section_name'],
                'section_slug': post_data['section_slug'],
                'preview_url': post_data['preview_url'],
                'preview_title': post_data['preview_title'],
                'preview_description': post_data['preview_description'],
                'preview_image_url': post_data['preview_image_url']
            })

        # OBTENER PUBLICACIONES COMPARTIDAS (MODIFICADO para previsualización)
        c.execute('''
            SELECT
                sp.id AS share_id, sp.timestamp AS share_timestamp_str, sp.quote_content,
                op.id AS original_post_id, op.user_id AS original_author_user_id,
                op.content AS original_content, op.image_filename AS original_image_filename,
                op.timestamp AS original_timestamp_str,
                original_author_profile.username AS original_author_username,
                original_author_profile.photo AS original_author_photo,
                original_author_profile.slug AS original_author_slug,
                s_orig.name AS original_section_name, s_orig.slug AS original_section_slug,
                op.preview_url, op.preview_title, op.preview_description, op.preview_image_url
            FROM shared_posts sp
            JOIN posts op ON sp.original_post_id = op.id
            LEFT JOIN sections s_orig ON op.section_id = s_orig.id
            JOIN users original_author_user ON op.user_id = original_author_user.id
            LEFT JOIN profiles original_author_profile ON op.user_id = original_author_profile.user_id
            WHERE sp.user_id = ?
            ORDER BY sp.timestamp DESC
        ''', (id_usuario_dueño_perfil,))
        shared_posts_by_profile_owner = c.fetchall()

        for shared_data in shared_posts_by_profile_owner:
            original_post_id = shared_data['original_post_id']
            share_timestamp_obj = parse_timestamp(shared_data['share_timestamp_str'])
            original_post_shared_timestamp_obj = parse_timestamp(shared_data['original_timestamp_str'])
            user_reaction_on_original = None
            if user_id_visitante:
                c.execute("SELECT reaction_type FROM post_reactions WHERE post_id = ? AND user_id = ?", (original_post_id, user_id_visitante))
                reaccion_usuario_o_row = c.fetchone()
                if reaccion_usuario_o_row: user_reaction_on_original = {'reaction_type': reaccion_usuario_o_row['reaction_type']}
            c.execute("SELECT COUNT(id) AS total_reactions FROM post_reactions WHERE post_id = ?", (original_post_id,))
            total_reactions_on_original_row = c.fetchone()
            total_reactions_on_original = total_reactions_on_original_row['total_reactions'] if total_reactions_on_original_row else 0
            c.execute("SELECT COUNT(id) FROM shared_posts WHERE original_post_id = ?", (original_post_id,))
            share_count_o_row = c.fetchone()
            share_count_original = share_count_o_row[0] if share_count_o_row else 0

            profile_items.append({
                'item_type': 'shared_post',
                'activity_timestamp': share_timestamp_obj,
                'share_id': shared_data['share_id'],
                'sharer_user_id': id_usuario_dueño_perfil,
                'sharer_username': nombre_display_dueño_perfil,
                'sharer_photo': foto_dueño_perfil,
                'sharer_slug': slug_dueño_perfil,
                'share_timestamp': share_timestamp_obj,
                'quote_content': shared_data['quote_content'],
                'original_post': {
                    'id': original_post_id,
                    'autor_id_post': shared_data['original_author_user_id'],
                    'username': (shared_data['original_author_username'] if shared_data['original_author_username'] and shared_data['original_author_username'].strip() else default_username_display),
                    'photo': shared_data['original_author_photo'],
                    'slug': (shared_data['original_author_slug'] if shared_data['original_author_slug'] and shared_data['original_author_slug'].strip() else "#"),
                    'content': procesar_menciones_para_mostrar(shared_data['original_content']),
                    'image_filename': shared_data['original_image_filename'],
                    'timestamp': original_post_shared_timestamp_obj,
                    'comments': [],
                    'total_reactions': total_reactions_on_original,
                    'user_reaction': user_reaction_on_original,
                    'share_count': share_count_original,
                    'section_name': shared_data['original_section_name'],
                    'section_slug': shared_data['original_section_slug'],
                    'preview_url': shared_data['preview_url'],
                    'preview_title': shared_data['preview_title'],
                    'preview_description': shared_data['preview_description'],
                    'preview_image_url': shared_data['preview_image_url']
                }
            })

        profile_items.sort(key=lambda item: item['activity_timestamp'] if item['activity_timestamp'] else datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        es_propio_perfil = (user_id_visitante == id_usuario_dueño_perfil) if user_id_visitante else False
        estado_contacto, puede_enviar_solicitud, solicitud_pendiente_de_este_perfil_al_visitante = None, False, None
        visitante_ha_bloqueado_a_perfil = False

        if user_id_visitante and not es_propio_perfil:
            c.execute('SELECT estado, solicitante_id FROM contactos WHERE (solicitante_id = ? AND receptor_id = ?) OR (solicitante_id = ? AND receptor_id = ?)',
                      (user_id_visitante, id_usuario_dueño_perfil, id_usuario_dueño_perfil, user_id_visitante))
            contacto_row = c.fetchone()
            if contacto_row:
                estado_contacto = contacto_row['estado']
                if estado_contacto == 'pendiente' and contacto_row['solicitante_id'] == id_usuario_dueño_perfil:
                    solicitud_pendiente_de_este_perfil_al_visitante = True
            else:
                puede_enviar_solicitud = True

            c.execute("SELECT 1 FROM blocked_users WHERE blocker_user_id = ? AND blocked_user_id = ?",
                      (user_id_visitante, id_usuario_dueño_perfil))
            if c.fetchone():
                visitante_ha_bloqueado_a_perfil = True


    return render_template('ver_perfil.html',
                           profile_user_id=id_usuario_dueño_perfil,
                           username_perfil=nombre_display_dueño_perfil,
                           bio=bio_dueño_perfil, photo=foto_dueño_perfil,
                           publicaciones=profile_items,
                           contacto_estado=estado_contacto,
                           puede_enviar_solicitud=puede_enviar_solicitud,
                           visitante_user_id=user_id_visitante,
                           slug_del_perfil=slug_dueño_perfil,
                           es_propio_perfil=es_propio_perfil,
                           solicitud_pendiente_aqui=solicitud_pendiente_de_este_perfil_al_visitante,
                           visitante_ha_bloqueado=visitante_ha_bloqueado_a_perfil)


@app.route('/post/<int:post_id_vista>')
def ver_publicacion_individual(post_id_vista):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para ver esta página.'), 'warning')
        return redirect(url_for('login'))
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil para ver las publicaciones.'), 'warning')
        return redirect(url_for('profile'))

    default_username_display = _("Usuario")
    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # MODIFICADO para obtener datos de previsualización
        c.execute('''
            SELECT p.id, p.user_id AS autor_id_post, pr.username, pr.photo, p.content,
                   p.image_filename, p.timestamp AS post_timestamp_str, pr.slug,
                   s.name AS section_name, s.slug AS section_slug,
                   p.preview_url, p.preview_title, p.preview_description, p.preview_image_url
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN profiles pr ON u.id = pr.user_id
            LEFT JOIN sections s ON p.section_id = s.id
            WHERE p.id = ?
        ''', (post_id_vista,))
        post_data_raw = c.fetchone()

        if not post_data_raw:
            flash(_('Publicación no encontrada.'), 'danger')
            return redirect(url_for('feed'))

        autor_id_post = post_data_raw['autor_id_post']
        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
        if autor_id_post in excluded_ids:
            flash(_('No puedes ver esta publicación debido a la configuración de bloqueo.'), 'danger')
            return redirect(url_for('feed'))

        post_timestamp_obj = parse_timestamp(post_data_raw['post_timestamp_str'])
        contenido_procesado_post = procesar_menciones_para_mostrar(post_data_raw['content'])
        autor_nombre_display = post_data_raw['username'] if post_data_raw['username'] and post_data_raw['username'].strip() else default_username_display
        autor_slug_display = post_data_raw['slug'] if post_data_raw['slug'] and post_data_raw['slug'].strip() else "#"

        c.execute("SELECT reaction_type FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_data_raw['id'], user_id_actual))
        reaccion_usuario_actual_row = c.fetchone()
        usuario_reacciono_info = {'reaction_type': reaccion_usuario_actual_row['reaction_type']} if reaccion_usuario_actual_row else None

        c.execute("SELECT COUNT(id) AS total_reactions FROM post_reactions WHERE post_id = ?", (post_data_raw['id'],))
        num_reacciones_totales_row = c.fetchone()
        num_reacciones_totales = num_reacciones_totales_row['total_reactions'] if num_reacciones_totales_row else 0

        c.execute("SELECT COUNT(id) FROM shared_posts WHERE original_post_id = ?", (post_data_raw['id'],))
        share_count_row = c.fetchone()
        share_count = share_count_row[0] if share_count_row else 0

        c.execute('''
            SELECT cm.id, pr_com.username, pr_com.photo, cm.content,
                   cm.timestamp AS comment_timestamp_str,
                   pr_com.slug, cm.parent_comment_id, cm.user_id AS comment_user_id
            FROM comments cm
            JOIN users u_com ON cm.user_id = u_com.id
            LEFT JOIN profiles pr_com ON u_com.id = pr_com.user_id
            WHERE cm.post_id = ?
            ORDER BY cm.timestamp ASC
        ''', (post_data_raw['id'],))
        comentarios_raw = c.fetchall()

        comments_map = {}
        structured_comments = []
        for row_com in comentarios_raw:
            comment_id = row_com['id']
            comment_timestamp_obj = parse_timestamp(row_com['comment_timestamp_str'])

            c.execute("SELECT COUNT(id) FROM comment_reactions WHERE comment_id = ?", (comment_id,))
            total_comment_reactions_row = c.fetchone()
            total_comment_reactions = total_comment_reactions_row[0] if total_comment_reactions_row else 0

            c.execute("SELECT reaction_type FROM comment_reactions WHERE comment_id = ? AND user_id = ?", (comment_id, user_id_actual))
            user_comment_reaction_row = c.fetchone()
            user_comment_reaction = {'reaction_type': user_comment_reaction_row['reaction_type']} if user_comment_reaction_row else None

            comments_map[comment_id] = {
                'id': comment_id,
                'username': (row_com['username'] if row_com['username'] and row_com['username'].strip() else default_username_display),
                'photo': row_com['photo'],
                'content': procesar_menciones_para_mostrar(row_com['content']),
                'timestamp': comment_timestamp_obj,
                'slug': (row_com['slug'] if row_com['slug'] and row_com['slug'].strip() else "#"),
                'parent_comment_id': row_com['parent_comment_id'],
                'user_id': row_com['comment_user_id'],
                'replies': [],
                'total_reactions': total_comment_reactions,
                'user_reaction': user_comment_reaction
            }

        for comment_id_map_key, comment_data_map in comments_map.items():
            parent_id_map = comment_data_map['parent_comment_id']
            if parent_id_map and parent_id_map in comments_map:
                comments_map[parent_id_map]['replies'].append(comment_data_map)
            else:
                structured_comments.append(comment_data_map)

        post_para_vista = {
            'id': post_data_raw['id'],
            'autor_id_post': post_data_raw['autor_id_post'],
            'username': autor_nombre_display,
            'photo': post_data_raw['photo'],
            'content': contenido_procesado_post,
            'image_filename': post_data_raw['image_filename'],
            'timestamp': post_timestamp_obj,
            'slug': autor_slug_display,
            'comments': structured_comments,
            'total_reactions': num_reacciones_totales,
            'user_reaction': usuario_reacciono_info,
            'share_count': share_count,
            'section_name': post_data_raw['section_name'],
            'section_slug': post_data_raw['section_slug'],
            'preview_url': post_data_raw['preview_url'],
            'preview_title': post_data_raw['preview_title'],
            'preview_description': post_data_raw['preview_description'],
            'preview_image_url': post_data_raw['preview_image_url']
        }
    return render_template('ver_post.html', post=post_para_vista)


@app.route('/notificaciones')
def notificaciones():
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para ver esta página.'), 'warning')
        return redirect(url_for('login'))
    user_id_actual = session['user_id']
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('SELECT id, mensaje, timestamp, leida, tipo, referencia_id FROM notificaciones WHERE user_id = ? ORDER BY leida ASC, timestamp DESC',
                  (user_id_actual,))
        notificaciones_list = c.fetchall()
    return render_template('notificaciones.html', notificaciones=notificaciones_list)


@app.route('/contactos')
def contactos():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil para ver tus contactos.'), 'warning')
        return redirect(url_for('profile'))

    search_term = request.args.get('q', '').strip()
    lista_de_contactos = []
    lista_de_bloqueados = []
    default_username_display = _("Usuario")

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
        params = [user_id_actual, user_id_actual]
        sql_query = '''
            SELECT pr.user_id, pr.username, pr.photo, pr.slug
            FROM profiles pr
            WHERE pr.user_id IN (
                SELECT c1.receptor_id FROM contactos c1 WHERE c1.solicitante_id = ? AND c1.estado = 'aceptado'
                UNION
                SELECT c2.solicitante_id FROM contactos c2 WHERE c2.receptor_id = ? AND c2.estado = 'aceptado'
            )
        '''

        if search_term:
            sql_query += " AND (LOWER(pr.username) LIKE LOWER(?) OR LOWER(pr.slug) LIKE LOWER(?)) "
            params.extend([f'%{search_term}%', f'%{search_term}%'])

        if excluded_ids:
            excluded_placeholders = ", ".join("?" for _ in excluded_ids)
            sql_query += f" AND pr.user_id NOT IN ({excluded_placeholders}) "
            params.extend(list(excluded_ids))

        sql_query += " ORDER BY pr.username ASC "

        c.execute(sql_query, tuple(params))
        contactos_raw = c.fetchall()
        for fila in contactos_raw:
            contact_user_id, contact_username_raw, contact_photo, contact_slug_raw = fila
            contact_username = contact_username_raw if contact_username_raw and contact_username_raw.strip() else default_username_display
            contact_slug = contact_slug_raw if contact_slug_raw and contact_slug_raw.strip() else "#"
            lista_de_contactos.append({
                'user_id': contact_user_id, 'username': contact_username,
                'photo': contact_photo, 'slug': contact_slug
            })

        c.execute('''
            SELECT pr.user_id, pr.username, pr.photo, pr.slug
            FROM profiles pr
            JOIN blocked_users bu ON pr.user_id = bu.blocked_user_id
            WHERE bu.blocker_user_id = ?
            ORDER BY pr.username ASC
        ''', (user_id_actual,))

        bloqueados_raw = c.fetchall()
        for fila in bloqueados_raw:
            blocked_user_id, blocked_username_raw, blocked_photo, blocked_slug_raw = fila
            blocked_username = blocked_username_raw if blocked_username_raw and blocked_username_raw.strip() else default_username_display
            blocked_slug = blocked_slug_raw if blocked_slug_raw and blocked_slug_raw.strip() else "#"
            lista_de_bloqueados.append({
                'user_id': blocked_user_id, 'username': blocked_username,
                'photo': blocked_photo, 'slug': blocked_slug
            })

    return render_template('contactos.html',
                           contactos=lista_de_contactos,
                           bloqueados=lista_de_bloqueados,
                           search_term=search_term)


@app.route('/mensajes')
def mensajes():
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para ver tus mensajes.'), 'warning')
        return redirect(url_for('login'))
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil para usar la mensajería.'), 'warning')
        return redirect(url_for('profile'))

    conversations_list = []
    default_username_display = _("Usuario")
    no_messages_yet_text = _("No hay mensajes todavía.")

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)

        c.execute('SELECT conversation_id FROM conversation_participants WHERE user_id = ?', (user_id_actual,))
        conversation_ids = [row[0] for row in c.fetchall()]

        for conv_id in conversation_ids:
            c.execute('SELECT user_id FROM conversation_participants WHERE conversation_id = ? AND user_id != ?', (conv_id, user_id_actual))
            other_participant_row = c.fetchone()
            if not other_participant_row: continue

            other_participant_id = other_participant_row[0]
            if other_participant_id in excluded_ids:
                continue

            c.execute('SELECT username, photo, slug FROM profiles WHERE user_id = ?', (other_participant_id,))
            other_profile_data = c.fetchone()

            if not other_profile_data:
                other_username, other_photo, other_slug = default_username_display, None, "#"
            else:
                other_username = other_profile_data[0] if other_profile_data[0] and other_profile_data[0].strip() else default_username_display
                other_photo = other_profile_data[1]
                other_slug = other_profile_data[2] if other_profile_data[2] and other_profile_data[2].strip() else "#"

            c.execute('SELECT body, sender_id, timestamp FROM messages WHERE conversation_id = ? ORDER BY timestamp DESC LIMIT 1', (conv_id,))
            last_message_row = c.fetchone()
            last_message_body, last_message_timestamp_str = no_messages_yet_text, ""

            if last_message_row:
                last_message_preview = (last_message_row[0][:40] + '...') if len(last_message_row[0]) > 40 else last_message_row[0]
                if last_message_row[1] == user_id_actual:
                    last_message_body = _("Tú: %(mensaje)s") % {'mensaje': last_message_preview}
                else:
                    last_message_body = last_message_preview
                last_message_timestamp_str = last_message_row[2]

            c.execute('SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND sender_id != ? AND is_read = 0', (conv_id, user_id_actual))
            unread_count = c.fetchone()[0]

            conversations_list.append({
                'conversation_id': conv_id,
                'other_user': {'username': other_username, 'photo': other_photo, 'slug': other_slug},
                'last_message': {'body': last_message_body, 'timestamp': last_message_timestamp_str},
                'unread_count': unread_count,
                'sort_timestamp': datetime.strptime(last_message_timestamp_str, '%Y-%m-%d %H:%M:%S.%f') if last_message_timestamp_str and '.' in last_message_timestamp_str else (datetime.strptime(last_message_timestamp_str, '%Y-%m-%d %H:%M:%S') if last_message_timestamp_str else datetime.min)
            })

        conversations_list.sort(key=lambda x: x['sort_timestamp'], reverse=True)

    return render_template('mensajes.html', conversations=conversations_list)


@app.route('/mensajes/iniciar/<int:receptor_id>', methods=['POST'])
def iniciar_conversacion(receptor_id):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para enviar mensajes.'), 'warning')
        return redirect(url_for('login'))
    user_id_actual = session['user_id']
    if user_id_actual == receptor_id:
        flash(_('No puedes iniciar una conversación contigo mismo.'), 'warning')
        return redirect(request.referrer or url_for('feed'))

    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()

        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
        if receptor_id in excluded_ids:
            flash(_('No puedes interactuar con este usuario.'), 'danger')
            return redirect(request.referrer or url_for('feed'))

        c.execute('SELECT id FROM contactos WHERE ((solicitante_id = ? AND receptor_id = ?) OR (solicitante_id = ? AND receptor_id = ?)) AND estado = \'aceptado\'',
                  (user_id_actual, receptor_id, receptor_id, user_id_actual))
        if not c.fetchone():
            c.execute('SELECT slug FROM profiles WHERE user_id = ?', (receptor_id,))
            receptor_profile_slug_row = c.fetchone()
            receptor_slug_for_redirect = receptor_profile_slug_row[0] if receptor_profile_slug_row else ''
            flash(_('Solo puedes enviar mensajes a tus contactos.'), 'danger')
            return redirect(url_for('ver_perfil', slug_perfil=request.form.get('receptor_slug', receptor_slug_for_redirect)))

        c.execute('''
            SELECT cp1.conversation_id
            FROM conversation_participants cp1
            JOIN conversation_participants cp2 ON cp1.conversation_id = cp2.conversation_id
            WHERE cp1.user_id = ? AND cp2.user_id = ?
        ''', (user_id_actual, receptor_id))
        existing_conversation = c.fetchone()

        if existing_conversation:
            return redirect(url_for('ver_conversacion', conversation_id=existing_conversation[0]))
        else:
            c.execute('INSERT INTO conversations DEFAULT VALUES')
            new_conversation_id = c.lastrowid
            c.execute('INSERT INTO conversation_participants (conversation_id, user_id) VALUES (?, ?)', (new_conversation_id, user_id_actual))
            c.execute('INSERT INTO conversation_participants (conversation_id, user_id) VALUES (?, ?)', (new_conversation_id, receptor_id))
            conn.commit()
            return redirect(url_for('ver_conversacion', conversation_id=new_conversation_id))


@app.route('/mensajes/<int:conversation_id>')
def ver_conversacion(conversation_id):
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para ver tus mensajes.'), 'warning')
        return redirect(url_for('login'))
    user_id_actual = session['user_id']
    default_username_display = _("Usuario")

    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT 1 FROM conversation_participants WHERE conversation_id = ? AND user_id = ?',
                  (conversation_id, user_id_actual))
        if not c.fetchone():
            flash(_('No tienes permiso para ver esta conversación.'), 'danger')
            return redirect(url_for('mensajes'))

        c.execute('''
            SELECT p.user_id, p.username, p.photo, p.slug
            FROM profiles p
            JOIN conversation_participants cp ON p.user_id = cp.user_id
            WHERE cp.conversation_id = ? AND cp.user_id != ?
        ''', (conversation_id, user_id_actual))
        other_user_data_raw = c.fetchone()

        if other_user_data_raw:
            other_user_id = other_user_data_raw['user_id']
            excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
            if other_user_id in excluded_ids:
                flash(_('No puedes ver esta conversación debido a la configuración de bloqueo.'), 'danger')
                return redirect(url_for('mensajes'))

            other_user_data = {
                'username': (other_user_data_raw['username'] if other_user_data_raw['username'] and other_user_data_raw['username'].strip() else default_username_display),
                'photo': other_user_data_raw['photo'],
                'slug': (other_user_data_raw['slug'] if other_user_data_raw['slug'] and other_user_data_raw['slug'].strip() else "#")
            }
        else:
             other_user_data = {'username': default_username_display, 'photo': None, 'slug': '#'}

        c.execute('UPDATE messages SET is_read = 1 WHERE conversation_id = ? AND sender_id != ? AND is_read = 0',
                  (conversation_id, user_id_actual))
        conn.commit()

        c.execute('''
            SELECT m.id, m.sender_id, m.body, m.timestamp, p.username as sender_username, p.photo as sender_photo
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            LEFT JOIN profiles p ON u.id = p.user_id
            WHERE m.conversation_id = ?
            ORDER BY m.timestamp ASC
        ''', (conversation_id,))
        messages_list_raw = c.fetchall()
        messages_list = []
        for msg_raw in messages_list_raw:
            messages_list.append({
                'id': msg_raw['id'], 'sender_id': msg_raw['sender_id'], 'body': msg_raw['body'],
                'timestamp': msg_raw['timestamp'],
                'username': (msg_raw['sender_username'] if msg_raw['sender_username'] and msg_raw['sender_username'].strip() else default_username_display),
                'photo': msg_raw['sender_photo']
            })

    return render_template('conversacion.html',
                           conversation_id=conversation_id, messages=messages_list,
                           other_user=other_user_data, current_user_id=user_id_actual)


@app.route('/api/notificacion/marcar_leida/<int:notificacion_id>', methods=['POST'])
def marcar_notificacion_leida(notificacion_id):
    if 'user_id' not in session:
        return jsonify(success=False, error=_("Autenticación requerida")), 401
    user_id_actual = session['user_id']
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE notificaciones
            SET leida = 1
            WHERE id = ? AND user_id = ? AND leida = 0
        ''', (notificacion_id, user_id_actual))
        if c.rowcount > 0:
            conn.commit()
            return jsonify(success=True)
        else:
            conn.rollback()
            return jsonify(success=False, error=_("Notificación no encontrada, ya marcada como leída o sin permiso.")), 404


@app.route('/api/mensajes/enviar', methods=['POST'])
def api_enviar_mensaje():
    if 'user_id' not in session:
        return jsonify(success=False, error=_("Autenticación requerida")), 401
    user_id_actual = session['user_id']
    data = request.get_json()
    conversation_id = data.get('conversation_id')
    body = data.get('body')

    if not conversation_id or not body or not body.strip():
        return jsonify(success=False, error=_("Faltan datos: ID de conversación y cuerpo del mensaje son requeridos.")), 400

    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT 1 FROM conversation_participants WHERE conversation_id = ? AND user_id = ?',
                  (conversation_id, user_id_actual))
        if not c.fetchone():
            return jsonify(success=False, error=_("No tienes permiso para enviar mensajes en esta conversación.")), 403

        c.execute('SELECT user_id FROM conversation_participants WHERE conversation_id = ? AND user_id != ?', (conversation_id, user_id_actual))
        other_user_row = c.fetchone()
        if other_user_row:
            other_user_id = other_user_row['user_id']
            excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
            if other_user_id in excluded_ids:
                 return jsonify(success=False, error=_("No puedes enviar mensajes a este usuario.")), 403

        timestamp_actual = datetime.utcnow()
        c.execute('INSERT INTO messages (conversation_id, sender_id, body, timestamp) VALUES (?, ?, ?, ?)',
                  (conversation_id, user_id_actual, body, timestamp_actual))
        id_nuevo_mensaje = c.lastrowid
        c.execute('UPDATE conversations SET updated_at = ? WHERE id = ?', (timestamp_actual, conversation_id))
        conn.commit()

        c.execute('SELECT username, photo FROM profiles WHERE user_id = ?', (user_id_actual,))
        sender_profile_raw = c.fetchone()
        sender_username = _("Usuario")
        if sender_profile_raw and sender_profile_raw['username'] and sender_profile_raw['username'].strip():
            sender_username = sender_profile_raw['username']

    return jsonify(
        success=True,
        message={
            'id': id_nuevo_mensaje, 'sender_id': user_id_actual, 'body': body,
            'timestamp': timestamp_actual.strftime('%Y-%m-%d %H:%M:%S'),
            'username': sender_username,
            'photo': sender_profile_raw['photo'] if sender_profile_raw else None
        }
    )


@app.route('/api/users/mention_search')
def mention_search():
    if 'user_id' not in session:
        return jsonify(error=_("Autenticación requerida")), 401

    search_term = request.args.get('term', '').strip()
    if not search_term or len(search_term) < 1: return jsonify([])

    current_user_id = session['user_id']
    suggestions = []
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        excluded_ids = get_blocked_and_blocking_ids(current_user_id, c)

        params = [f'%{search_term}%', f'%{search_term}%', current_user_id]
        not_in_clause = ""
        if excluded_ids:
            excluded_placeholders = ", ".join("?" for _ in excluded_ids)
            not_in_clause = f" AND user_id NOT IN ({excluded_placeholders}) "
            params.extend(list(excluded_ids))

        query = f"""
            SELECT username, slug, photo FROM profiles
            WHERE (LOWER(username) LIKE LOWER(?) OR LOWER(slug) LIKE LOWER(?))
            AND user_id != ? AND username IS NOT NULL AND username != ''
            AND slug IS NOT NULL AND slug != '' {not_in_clause}
            ORDER BY username ASC LIMIT 10
        """
        c.execute(query, tuple(params))
        users_found = c.fetchall()
        for row in users_found:
            photo_filename = row[2]
            photo_url = url_for('static', filename=f'uploads/{photo_filename}', _external=False) if photo_filename else None
            suggestions.append({'username': row[0], 'slug': row[1], 'photo': photo_url})
    return jsonify(suggestions)


@app.route('/sections')
def sections_list():
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para ver esta página.'), 'warning')
        return redirect(url_for('login'))
    if not check_profile_completion(session['user_id']):
        flash(_('Por favor, completa tu perfil para ver las secciones.'), 'warning')
        return redirect(url_for('profile'))

    all_sections = []
    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id, name, slug, description, icon_filename FROM sections ORDER BY name ASC")
        sections_raw = c.fetchall()
        for section_row in sections_raw:
            all_sections.append(dict(section_row))

    return render_template('sections_list.html', sections=all_sections)


@app.route('/section/<slug_seccion>')
def view_section(slug_seccion):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Debes completar tu perfil para ver el contenido de las secciones.'), 'warning')
        return redirect(url_for('profile'))

    feed_items = []
    default_username_display = _("Usuario")
    all_sections = []

    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute("SELECT id, name FROM sections WHERE slug = ?", (slug_seccion,))
        section_data_db = c.fetchone()
        if not section_data_db:
            flash(_('Sección no encontrada.'), 'danger')
            return redirect(url_for('sections_list'))

        current_section_id = section_data_db['id']
        current_section_name = section_data_db['name']

        c.execute("SELECT id, name, slug FROM sections ORDER BY name ASC")
        sections_raw_for_form = c.fetchall()
        for section_row_form in sections_raw_for_form:
            all_sections.append(dict(section_row_form))

        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)

        params = [current_section_id]
        where_clauses_original = ["p.section_id = ?"]
        if excluded_ids:
            excluded_placeholders = ", ".join("?" for _ in excluded_ids)
            where_clauses_original.append(f"p.user_id NOT IN ({excluded_placeholders})")
            params.extend(list(excluded_ids))

        # MODIFICADO para obtener datos de previsualización
        query_original_posts = f'''
            SELECT p.id, p.user_id AS autor_id_post, pr.username, pr.photo, p.content,
                   p.image_filename, p.timestamp AS post_timestamp_str, pr.slug,
                   s.name AS section_name, s.slug AS section_slug,
                   p.preview_url, p.preview_title, p.preview_description, p.preview_image_url
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN profiles pr ON u.id = pr.user_id
            LEFT JOIN sections s ON p.section_id = s.id
            WHERE {" AND ".join(where_clauses_original)}
            ORDER BY p.timestamp DESC
            LIMIT 50
        '''
        c.execute(query_original_posts, tuple(params))
        original_posts_raw = c.fetchall()

        for post_data in original_posts_raw:
            post_id = post_data['id']
            post_timestamp_obj = parse_timestamp(post_data['post_timestamp_str'])
            # ... (Lógica de reacciones, comentarios, etc. idéntica a feed())
            c.execute("SELECT reaction_type FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id_actual))
            reaccion_usuario_actual_row = c.fetchone()
            usuario_reacciono_info = {'reaction_type': reaccion_usuario_actual_row['reaction_type']} if reaccion_usuario_actual_row else None
            c.execute("SELECT COUNT(id) AS total_reactions FROM post_reactions WHERE post_id = ?", (post_id,))
            num_reacciones_totales_row = c.fetchone()
            num_reacciones_totales = num_reacciones_totales_row['total_reactions'] if num_reacciones_totales_row else 0
            c.execute("SELECT COUNT(id) FROM shared_posts WHERE original_post_id = ?", (post_id,))
            share_count_row = c.fetchone()
            share_count = share_count_row[0] if share_count_row else 0
            c.execute('''
                SELECT cm.id, pr_com.username AS comment_username, pr_com.photo AS comment_photo,
                       cm.content AS comment_content, cm.timestamp AS comment_timestamp_str,
                       pr_com.slug AS comment_slug, cm.parent_comment_id, cm.user_id AS comment_user_id
                FROM comments cm
                JOIN users u_com ON cm.user_id = u_com.id LEFT JOIN profiles pr_com ON u_com.id = pr_com.user_id
                WHERE cm.post_id = ? ORDER BY cm.timestamp ASC
            ''', (post_id,))
            comentarios_raw = c.fetchall()
            comments_map = {}
            structured_comments = []
            for row_com in comentarios_raw:
                comment_id = row_com['id']
                comment_timestamp_obj = parse_timestamp(row_com['comment_timestamp_str'])
                c.execute("SELECT COUNT(id) FROM comment_reactions WHERE comment_id = ?", (comment_id,))
                total_comment_reactions_row = c.fetchone()
                total_comment_reactions = total_comment_reactions_row[0] if total_comment_reactions_row else 0
                c.execute("SELECT reaction_type FROM comment_reactions WHERE comment_id = ? AND user_id = ?", (comment_id, user_id_actual))
                user_cr_row = c.fetchone()
                user_comment_reaction = {'reaction_type': user_cr_row['reaction_type']} if user_cr_row else None
                comments_map[comment_id] = {
                    'id': comment_id, 'username': (row_com['comment_username'] if row_com['comment_username'] and row_com['comment_username'].strip() else default_username_display),
                    'photo': row_com['comment_photo'], 'content': procesar_menciones_para_mostrar(row_com['comment_content']),
                    'timestamp': comment_timestamp_obj, 'slug': (row_com['comment_slug'] if row_com['comment_slug'] and row_com['comment_slug'].strip() else "#"),
                    'parent_comment_id': row_com['parent_comment_id'], 'user_id': row_com['comment_user_id'],
                    'replies': [], 'total_reactions': total_comment_reactions, 'user_reaction': user_comment_reaction
                }
            for cid, cdata in comments_map.items():
                if cdata['parent_comment_id'] and cdata['parent_comment_id'] in comments_map:
                    comments_map[cdata['parent_comment_id']]['replies'].append(cdata)
                else: structured_comments.append(cdata)

            feed_items.append({
                'item_type': 'original_post',
                'activity_timestamp': post_timestamp_obj,
                'id': post_id,
                'autor_id_post': post_data['autor_id_post'],
                'username': (post_data['username'] if post_data['username'] and post_data['username'].strip() else default_username_display),
                'photo': post_data['photo'],
                'slug': (post_data['slug'] if post_data['slug'] and post_data['slug'].strip() else "#"),
                'content': procesar_menciones_para_mostrar(post_data['content']),
                'image_filename': post_data['image_filename'],
                'timestamp': post_timestamp_obj,
                'comments': structured_comments,
                'total_reactions': num_reacciones_totales,
                'user_reaction': usuario_reacciono_info,
                'share_count': share_count,
                'section_name': post_data['section_name'],
                'section_slug': post_data['section_slug'],
                'preview_url': post_data['preview_url'],
                'preview_title': post_data['preview_title'],
                'preview_description': post_data['preview_description'],
                'preview_image_url': post_data['preview_image_url']
            })

        feed_items.sort(key=lambda item: item['activity_timestamp'] if item.get('activity_timestamp') else datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return render_template('view_section.html',
                           posts=feed_items,
                           section_name=current_section_name,
                           section_slug=slug_seccion,
                           sections=all_sections)

# ... (todas tus rutas existentes) ...

# ... (dentro de la función search en app.py) ...

# Asegúrate de tener "import re" y "from math import ceil" al principio de tu app.py

@app.route('/search')
def search():
    if 'user_id' not in session:
        flash(_('Debes iniciar sesión para realizar búsquedas.'), 'warning')
        return redirect(url_for('login'))
    
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil para usar la búsqueda.'), 'warning')
        return redirect(url_for('profile'))

    query = request.args.get('q', '').strip()
    search_results = []
    default_username_display = _("Usuario")

    if not query:
        # Aunque podrías mostrar un mensaje, usualmente es mejor renderizar la página
        # search_results.html vacía o con un mensaje dentro de ella.
        return render_template('search_results.html', posts=[], query=query)

    with sqlite3.connect('users.db') as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        excluded_ids = get_blocked_and_blocking_ids(user_id_actual, c)
        excluded_placeholders = ""
        if excluded_ids:
            excluded_placeholders = ", ".join("?" for _ in excluded_ids)

        # 1. BUSCAR EN PUBLICACIONES ORIGINALES (content)
        params_original = [f'%{query}%']
        where_clause_original_list = ["LOWER(p.content) LIKE LOWER(?)"]
        if excluded_ids:
            where_clause_original_list.append(f"p.user_id NOT IN ({excluded_placeholders})")
            params_original.extend(list(excluded_ids))
        
        where_clause_original_str = " AND ".join(where_clause_original_list)

        sql_original_posts = f'''
            SELECT p.id, p.user_id AS autor_id_post, pr.username, pr.photo, p.content,
                   p.image_filename, p.timestamp AS post_timestamp_str, pr.slug,
                   s.name AS section_name, s.slug AS section_slug,
                   p.preview_url, p.preview_title, p.preview_description, p.preview_image_url
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN profiles pr ON u.id = pr.user_id
            LEFT JOIN sections s ON p.section_id = s.id
            WHERE {where_clause_original_str}
            ORDER BY p.timestamp DESC
            LIMIT 30 
        '''
        c.execute(sql_original_posts, tuple(params_original))
        original_posts_found = c.fetchall() # <--- DEFINICIÓN CLAVE

        for post_data in original_posts_found:
            post_id = post_data['id']
            post_timestamp_obj = parse_timestamp(post_data['post_timestamp_str'])
            
            # Obtener reacciones del usuario actual para este post
            c.execute("SELECT reaction_type FROM post_reactions WHERE post_id = ? AND user_id = ?", (post_id, user_id_actual))
            reaccion_usuario_actual_row = c.fetchone()
            usuario_reacciono_info = {'reaction_type': reaccion_usuario_actual_row['reaction_type']} if reaccion_usuario_actual_row else None
            
            # Obtener total de reacciones para este post
            c.execute("SELECT COUNT(id) AS total_reactions FROM post_reactions WHERE post_id = ?", (post_id,))
            num_reacciones_totales_row = c.fetchone()
            num_reacciones_totales = num_reacciones_totales_row['total_reactions'] if num_reacciones_totales_row else 0
            
            # Obtener conteo de compartidos
            c.execute("SELECT COUNT(id) FROM shared_posts WHERE original_post_id = ?", (post_id,))
            share_count_row = c.fetchone()
            share_count = share_count_row[0] if share_count_row else 0
            
            # Obtener y estructurar comentarios
            c.execute('''
                SELECT cm.id, pr_com.username AS comment_username, pr_com.photo AS comment_photo, 
                       cm.content AS comment_content, cm.timestamp AS comment_timestamp_str, 
                       pr_com.slug AS comment_slug, cm.parent_comment_id, cm.user_id AS comment_user_id
                FROM comments cm
                JOIN users u_com ON cm.user_id = u_com.id LEFT JOIN profiles pr_com ON u_com.id = pr_com.user_id
                WHERE cm.post_id = ? ORDER BY cm.timestamp ASC
            ''', (post_id,))
            comentarios_raw = c.fetchall()
            comments_map = {}
            structured_comments = []
            for row_com in comentarios_raw:
                comment_id = row_com['id']
                comment_timestamp_obj = parse_timestamp(row_com['comment_timestamp_str'])
                c.execute("SELECT COUNT(id) FROM comment_reactions WHERE comment_id = ?", (comment_id,))
                total_comment_reactions_row = c.fetchone()
                total_comment_reactions = total_comment_reactions_row[0] if total_comment_reactions_row else 0
                c.execute("SELECT reaction_type FROM comment_reactions WHERE comment_id = ? AND user_id = ?", (comment_id, user_id_actual))
                user_cr_row = c.fetchone()
                user_comment_reaction = {'reaction_type': user_cr_row['reaction_type']} if user_cr_row else None
                comments_map[comment_id] = {
                    'id': comment_id, 'username': (row_com['comment_username'] if row_com['comment_username'] and row_com['comment_username'].strip() else default_username_display),
                    'photo': row_com['comment_photo'], 'content': procesar_menciones_para_mostrar(row_com['comment_content']),
                    'timestamp': comment_timestamp_obj, 'slug': (row_com['comment_slug'] if row_com['comment_slug'] and row_com['comment_slug'].strip() else "#"),
                    'parent_comment_id': row_com['parent_comment_id'], 'user_id': row_com['comment_user_id'],
                    'replies': [], 'total_reactions': total_comment_reactions, 'user_reaction': user_comment_reaction
                }
            for cid_map_key, cdata_map_val in comments_map.items():
                if cdata_map_val['parent_comment_id'] and cdata_map_val['parent_comment_id'] in comments_map:
                    comments_map[cdata_map_val['parent_comment_id']]['replies'].append(cdata_map_val)
                else:
                    structured_comments.append(cdata_map_val)

            content_with_mentions = procesar_menciones_para_mostrar(post_data['content'])
            highlighted_content = highlight_term(content_with_mentions, query)

            search_results.append({
                'item_type': 'original_post',
                'activity_timestamp': post_timestamp_obj,
                'id': post_id,
                'autor_id_post': post_data['autor_id_post'],
                'username': (post_data['username'] if post_data['username'] and post_data['username'].strip() else default_username_display),
                'photo': post_data['photo'],
                'slug': (post_data['slug'] if post_data['slug'] and post_data['slug'].strip() else "#"),
                'content': highlighted_content,
                'image_filename': post_data['image_filename'],
                'timestamp': post_timestamp_obj,
                'comments': structured_comments,
                'total_reactions': num_reacciones_totales,
                'user_reaction': usuario_reacciono_info,
                'share_count': share_count,
                'section_name': post_data['section_name'],
                'section_slug': post_data['section_slug'],
                'preview_url': post_data['preview_url'],
                'preview_title': post_data['preview_title'],
                'preview_description': post_data['preview_description'],
                'preview_image_url': post_data['preview_image_url']
            })

        # 2. BUSCAR EN PUBLICACIONES COMPARTIDAS (quote_content)
        params_shared = [f'%{query}%']
        where_clauses_shared_search_list = ["LOWER(sp.quote_content) LIKE LOWER(?)"]
        if excluded_ids:
            where_clauses_shared_search_list.append(f"sp.user_id NOT IN ({excluded_placeholders})")
            params_shared.extend(list(excluded_ids))
            where_clauses_shared_search_list.append(f"op.user_id NOT IN ({excluded_placeholders})")
            params_shared.extend(list(excluded_ids))

        where_clauses_shared_search_str = " AND ".join(where_clauses_shared_search_list)

        sql_shared_posts = f'''
            SELECT
                sp.id AS share_id, sp.timestamp AS share_timestamp_str, sp.user_id AS sharer_user_id,
                sharer_profile.username AS sharer_username, sharer_profile.photo AS sharer_photo,
                sharer_profile.slug AS sharer_slug,
                sp.quote_content,
                op.id AS original_post_id, op.user_id AS original_author_user_id,
                op.content AS original_content, op.image_filename AS original_image_filename,
                op.timestamp AS original_timestamp_str,
                original_author_profile.username AS original_author_username,
                original_author_profile.photo AS original_author_photo,
                original_author_profile.slug AS original_author_slug,
                s_orig.name AS original_section_name, s_orig.slug AS original_section_slug,
                op.preview_url, op.preview_title, op.preview_description, op.preview_image_url
            FROM shared_posts sp
            JOIN users sharer_user ON sp.user_id = sharer_user.id
            LEFT JOIN profiles sharer_profile ON sp.user_id = sharer_profile.user_id
            JOIN posts op ON sp.original_post_id = op.id
            LEFT JOIN sections s_orig ON op.section_id = s_orig.id
            JOIN users original_author_user ON op.user_id = original_author_user.id
            LEFT JOIN profiles original_author_profile ON op.user_id = original_author_profile.user_id
            WHERE {where_clauses_shared_search_str}
            ORDER BY sp.timestamp DESC
            LIMIT 30
        '''
        c.execute(sql_shared_posts, tuple(params_shared))
        shared_posts_found = c.fetchall() # <--- DEFINICIÓN CLAVE

        for shared_data in shared_posts_found:
            original_post_id = shared_data['original_post_id']
            share_timestamp_obj = parse_timestamp(shared_data['share_timestamp_str'])
            original_timestamp_obj = parse_timestamp(shared_data['original_timestamp_str'])

            # Lógica para reacciones, comentarios, etc., para el post original dentro del compartido
            c.execute("SELECT reaction_type FROM post_reactions WHERE post_id = ? AND user_id = ?", (original_post_id, user_id_actual))
            reaccion_usuario_original_row = c.fetchone()
            usuario_reacciono_original_info = {'reaction_type': reaccion_usuario_original_row['reaction_type']} if reaccion_usuario_original_row else None
            
            c.execute("SELECT COUNT(id) AS total_reactions FROM post_reactions WHERE post_id = ?", (original_post_id,))
            num_reacciones_original_totales_row = c.fetchone()
            num_reacciones_original_totales = num_reacciones_original_totales_row['total_reactions'] if num_reacciones_original_totales_row else 0
            
            c.execute("SELECT COUNT(id) FROM shared_posts WHERE original_post_id = ?", (original_post_id,))
            share_count_original_row = c.fetchone()
            share_count_original = share_count_original_row[0] if share_count_original_row else 0
            
            c.execute('''
                SELECT cm.id, pr_com.username AS comment_username, pr_com.photo AS comment_photo, 
                       cm.content AS comment_content, cm.timestamp AS comment_timestamp_str, 
                       pr_com.slug AS comment_slug, cm.parent_comment_id, cm.user_id AS comment_user_id
                FROM comments cm
                JOIN users u_com ON cm.user_id = u_com.id LEFT JOIN profiles pr_com ON u_com.id = pr_com.user_id
                WHERE cm.post_id = ? ORDER BY cm.timestamp ASC
            ''', (original_post_id,))
            comentarios_original_raw = c.fetchall()
            comments_original_map = {}
            structured_original_comments = []
            for row_com_orig in comentarios_original_raw:
                comment_id_orig = row_com_orig['id']
                comment_original_timestamp_obj = parse_timestamp(row_com_orig['comment_timestamp_str'])
                c.execute("SELECT COUNT(id) FROM comment_reactions WHERE comment_id = ?", (comment_id_orig,))
                total_comment_reactions_o_row = c.fetchone()
                total_comment_reactions_o = total_comment_reactions_o_row[0] if total_comment_reactions_o_row else 0
                c.execute("SELECT reaction_type FROM comment_reactions WHERE comment_id = ? AND user_id = ?", (comment_id_orig, user_id_actual))
                user_cr_o_row = c.fetchone()
                user_comment_reaction_o = {'reaction_type': user_cr_o_row['reaction_type']} if user_cr_o_row else None
                comments_original_map[comment_id_orig] = {
                    'id': comment_id_orig, 'username': (row_com_orig['comment_username'] if row_com_orig['comment_username'] and row_com_orig['comment_username'].strip() else default_username_display),
                    'photo': row_com_orig['comment_photo'], 'content': procesar_menciones_para_mostrar(row_com_orig['comment_content']),
                    'timestamp': comment_original_timestamp_obj, 'slug': (row_com_orig['comment_slug'] if row_com_orig['comment_slug'] and row_com_orig['comment_slug'].strip() else "#"),
                    'parent_comment_id': row_com_orig['parent_comment_id'], 'user_id': row_com_orig['comment_user_id'],
                    'replies': [], 'total_reactions': total_comment_reactions_o, 'user_reaction': user_comment_reaction_o
                }
            for cid_o_map_key, cdata_o_map_val in comments_original_map.items():
                if cdata_o_map_val['parent_comment_id'] and cdata_o_map_val['parent_comment_id'] in comments_original_map:
                    comments_original_map[cdata_o_map_val['parent_comment_id']]['replies'].append(cdata_o_map_val)
                else:
                    structured_original_comments.append(cdata_o_map_val)


            quote_content_with_mentions = procesar_menciones_para_mostrar(shared_data['quote_content'])
            highlighted_quote = highlight_term(quote_content_with_mentions, query)
            original_post_content_with_mentions = procesar_menciones_para_mostrar(shared_data['original_content'])

            search_results.append({
                'item_type': 'shared_post',
                'activity_timestamp': share_timestamp_obj,
                'share_id': shared_data['share_id'],
                'sharer_user_id': shared_data['sharer_user_id'],
                'sharer_username': (shared_data['sharer_username'] if shared_data['sharer_username'] and shared_data['sharer_username'].strip() else default_username_display),
                'sharer_photo': shared_data['sharer_photo'],
                'sharer_slug': (shared_data['sharer_slug'] if shared_data['sharer_slug'] and shared_data['sharer_slug'].strip() else "#"),
                'share_timestamp': share_timestamp_obj,
                'quote_content': highlighted_quote,
                'original_post': {
                    'id': original_post_id,
                    'autor_id_post': shared_data['original_author_user_id'],
                    'username': (shared_data['original_author_username'] if shared_data['original_author_username'] and shared_data['original_author_username'].strip() else default_username_display),
                    'photo': shared_data['original_author_photo'],
                    'slug': (shared_data['original_author_slug'] if shared_data['original_author_slug'] and shared_data['original_author_slug'].strip() else "#"),
                    'content': original_post_content_with_mentions,
                    'image_filename': shared_data['original_image_filename'],
                    'timestamp': original_timestamp_obj,
                    'comments': structured_original_comments,
                    'total_reactions': num_reacciones_original_totales,
                    'user_reaction': usuario_reacciono_original_info,
                    'share_count': share_count_original,
                    'section_name': shared_data['original_section_name'],
                    'section_slug': shared_data['original_section_slug'],
                    'preview_url': shared_data['preview_url'],
                    'preview_title': shared_data['preview_title'],
                    'preview_description': shared_data['preview_description'],
                    'preview_image_url': shared_data['preview_image_url']
                }
            })
            
        search_results.sort(key=lambda item: item['activity_timestamp'] if item.get('activity_timestamp') else datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return render_template('search_results.html', posts=search_results, query=query)

def highlight_term(text_content, query):
    if not query or not text_content:
        return text_content
    try:
        # Escapar el query para que los caracteres especiales se traten literalmente en la regex
        escaped_query = re.escape(query)
        # Compilar la regex para buscar el query (case-insensitive)
        # La función de reemplazo usará el texto original encontrado (\g<0>) para mantener su casing
        highlighted_text = re.sub(
            f'({escaped_query})',
            r'<span class="search-query-highlight">\g<0></span>',
            text_content,
            flags=re.IGNORECASE
        )
        return highlighted_text
    except Exception as e:
        print(f"Error durante el resaltado: {e}")
        return text_content

if __name__ == '__main__':
    print("Iniciando la base de datos y la aplicación web...")
    init_db()
    regenerar_slugs_si_faltan()
    print(f"Servidor '{app.name}' ejecutándose en http://localhost:5000")
    app.run(debug=True)
    print("DEBUG: init_db() completado.")