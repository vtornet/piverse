from flask import (Flask, render_template, request, redirect, session, 
                   url_for, flash, jsonify, Response, send_from_directory)
from flask_babel import Babel, gettext as _, lazy_gettext as _l, get_locale as get_babel_locale, \
                        format_datetime, format_date, format_time, format_timedelta, format_number
from functools import wraps
import os
import re
import requests
import urllib.parse
import time
from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timezone, timedelta

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine, event, text, or_, and_, desc, asc, func, union_all
from sqlalchemy.engine import Engine
from sqlalchemy.orm import joinedload, aliased
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'clave-secreta-para-desarrollo-local')

# --- CONFIGURACIÓN DE LA BASE DE DATOS ---
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
elif not DATABASE_URL:
    DATABASE_URL = "sqlite:///" + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'users.db')

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
}

db = SQLAlchemy(app)

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# --- MODELOS DE LA BASE DE DATOS ---

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')
    pi_uid = db.Column(db.String(255), unique=True, nullable=True)
    banned_until = db.Column(db.DateTime(timezone=True), nullable=True)
    ban_reason = db.Column(db.Text, nullable=True)
    muted_until = db.Column(db.DateTime(timezone=True), nullable=True)
    accepted_policies = db.Column(db.Boolean, default=False, nullable=False)
    
    profile = db.relationship('Profile', backref='user', uselist=False, cascade="all, delete-orphan")
    posts = db.relationship('Post', backref='author', lazy='dynamic', cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='author', lazy='dynamic', cascade="all, delete-orphan")
    post_reactions = db.relationship('PostReaction', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    comment_reactions = db.relationship('CommentReaction', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    sent_contact_requests = db.relationship('Contact', foreign_keys='Contact.solicitante_id', backref='solicitante', lazy='dynamic', cascade="all, delete-orphan")
    received_contact_requests = db.relationship('Contact', foreign_keys='Contact.receptor_id', backref='receptor', lazy='dynamic', cascade="all, delete-orphan")
    notifications = db.relationship('Notification', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    shared_posts = db.relationship('SharedPost', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    blocked_by = db.relationship('BlockedUser', foreign_keys='BlockedUser.blocker_user_id', backref='blocker_user', lazy='dynamic', cascade="all, delete-orphan")
    blocked_users = db.relationship('BlockedUser', foreign_keys='BlockedUser.blocked_user_id', backref='blocked_user', lazy='dynamic', cascade="all, delete-orphan")
    action_logs_actor = db.relationship('ActionLog', foreign_keys='ActionLog.actor_user_id', backref='actor_user', lazy='dynamic')
    action_logs_target = db.relationship('ActionLog', foreign_keys='ActionLog.target_user_id', backref='target_user', lazy='dynamic')
    reports_by_user = db.relationship('Report', foreign_keys='Report.reporter_user_id', backref='reporter_user', lazy='dynamic')
    reports_reviewed_by = db.relationship('Report', foreign_keys='Report.reviewed_by_user_id', backref='reviewed_by_user', lazy='dynamic')
    appeals_by_user = db.relationship('Appeal', foreign_keys='Appeal.user_id', backref='appellant_user', lazy='dynamic', cascade="all, delete-orphan")
    appeals_reviewed_by = db.relationship('Appeal', foreign_keys='Appeal.reviewed_by_user_id', backref='appeal_reviewer', lazy='dynamic')
    sent_messages = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy='dynamic')
    
class Profile(db.Model):
    __tablename__ = 'profiles'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=True)
    bio = db.Column(db.Text, nullable=True)
    photo = db.Column(db.String(255), nullable=True)
    slug = db.Column(db.String(100), unique=True, nullable=True)

class Section(db.Model):
    __tablename__ = 'sections'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    icon_filename = db.Column(db.String(255), nullable=True)
    posts = db.relationship('Post', backref='section', lazy='dynamic')

class Post(db.Model):
    __tablename__ = 'posts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id', ondelete='SET NULL'), nullable=True)
    preview_url = db.Column(db.Text, nullable=True)
    preview_title = db.Column(db.Text, nullable=True)
    preview_description = db.Column(db.Text, nullable=True)
    preview_image_url = db.Column(db.Text, nullable=True)
    is_visible = db.Column(db.Boolean, default=True, nullable=False)
    comments = db.relationship('Comment', backref='post', lazy='dynamic', cascade="all, delete-orphan")
    reactions = db.relationship('PostReaction', backref='post', lazy='dynamic', cascade="all, delete-orphan")
    shared = db.relationship('SharedPost', foreign_keys='SharedPost.original_post_id', backref='original_post', lazy='dynamic', cascade="all, delete-orphan")

class Comment(db.Model):
    __tablename__ = 'comments'
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    parent_comment_id = db.Column(db.Integer, db.ForeignKey('comments.id', ondelete='CASCADE'), nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_visible = db.Column(db.Boolean, default=True, nullable=False)
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), lazy='dynamic', cascade="all, delete-orphan")
    reactions = db.relationship('CommentReaction', backref='comment', lazy='dynamic', cascade="all, delete-orphan")

class PostReaction(db.Model):
    __tablename__ = 'post_reactions'
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    reaction_type = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('post_id', 'user_id'),)

class CommentReaction(db.Model):
    __tablename__ = 'comment_reactions'
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comments.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    reaction_type = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('comment_id', 'user_id'),)
    
class SharedPost(db.Model):
    __tablename__ = 'shared_posts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    original_post_id = db.Column(db.Integer, db.ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    quote_content = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('user_id', 'original_post_id'),)

class BlockedUser(db.Model):
    __tablename__ = 'blocked_users'
    id = db.Column(db.Integer, primary_key=True)
    blocker_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    blocked_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('blocker_user_id', 'blocked_user_id'),)

class Contact(db.Model):
    __tablename__ = 'contactos'
    id = db.Column(db.Integer, primary_key=True)
    solicitante_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    receptor_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    estado = db.Column(db.String(50), default='pendiente')
    __table_args__ = (db.UniqueConstraint('solicitante_id', 'receptor_id'),)

class Notification(db.Model):
    __tablename__ = 'notificaciones'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    mensaje = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.String(50), nullable=True)
    referencia_id = db.Column(db.Integer, nullable=True)
    leida = db.Column(db.Boolean, default=False, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Conversation(db.Model):
    __tablename__ = 'conversations'
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    participants = db.relationship('ConversationParticipant', backref='conversation', lazy='dynamic', cascade="all, delete-orphan")
    messages = db.relationship('Message', backref='conversation', lazy='dynamic', cascade="all, delete-orphan")

class ConversationParticipant(db.Model):
    __tablename__ = 'conversation_participants'
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    __table_args__ = (db.UniqueConstraint('conversation_id', 'user_id'),)

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    
class ActionLog(db.Model):
    __tablename__ = 'action_logs'
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    action_type = db.Column(db.String(100), nullable=False)
    target_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    target_content_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.Text, nullable=True)

class Report(db.Model):
    __tablename__ = 'reports'
    id = db.Column(db.Integer, primary_key=True)
    reporter_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    content_type = db.Column(db.String(50), nullable=False)
    content_id = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    details = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False, default='pending')
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    appeal = db.relationship('Appeal', backref='original_report', uselist=False, cascade="all, delete-orphan")

class Appeal(db.Model):
    __tablename__ = 'appeals'
    id = db.Column(db.Integer, primary_key=True)
    original_report_id = db.Column(db.Integer, db.ForeignKey('reports.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    appeal_text = db.Column(db.Text, nullable=False)
    appeal_image_filename = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='pending')
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    
# --- CONSTANTES Y CONFIGURACIÓN ---
POSTS_PER_PAGE = 10
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

upload_path = os.path.join(app.root_path, UPLOAD_FOLDER)
os.makedirs(upload_path, exist_ok=True)
POST_IMAGES_FOLDER = os.path.join(app.config['UPLOAD_FOLDER'], 'post_images')
os.makedirs(POST_IMAGES_FOLDER, exist_ok=True)
APPEAL_IMAGES_FOLDER = os.path.join(app.config['UPLOAD_FOLDER'], 'appeal_images')
os.makedirs(APPEAL_IMAGES_FOLDER, exist_ok=True)

# --- RESPUESTAS PREDEFINIDAS PARA REPORTES ---
PREDEFINED_UPHOLD_REASONS = {
    "spam": "Hemos revisado tu contenido y hemos determinado que infringe nuestras normas sobre spam y autopromoción no deseada.",
    "hate_speech": "Este contenido ha sido eliminado porque infringe nuestras políticas sobre discurso de odio y lenguaje que incita a la violencia.",
    "harassment": "Hemos determinado que este contenido constituye acoso o bullying hacia otro miembro de la comunidad, lo cual no está permitido.",
    "inappropriate_content": "Este contenido ha sido eliminado por ser explícito o inapropiado para nuestra comunidad."
}
PREDEFINED_DISMISS_REASONS = {
    "not_a_violation": "Gracias por tu reporte. Tras revisarlo, hemos determinado que el contenido no infringe nuestras normas comunitarias.",
    "insufficient_context": "Gracias por tu reporte. No hemos podido tomar una decisión con la información proporcionada, ya que el contenido requiere más contexto.",
    "user_blocked": "Gracias por tu reporte. Te recomendamos que, además de reportar, uses la función de bloqueo si no deseas ver el contenido de este usuario."
}
PREDEFINED_APPEAL_APPROVAL_REASONS = {
    "re-evaluation_ok": "Tras una segunda revisión por parte del equipo de administración, hemos determinado que tu apelación es válida y la decisión original ha sido revertida. Lamentamos los inconvenientes.",
    "new_context_ok": "Gracias por aportar nuevo contexto en tu apelación. Hemos re-evaluado el caso y te damos la razón. La sanción ha sido retirada."
}
PREDEFINED_APPEAL_DENIAL_REASONS = {
    "decision_upheld": "Tras una revisión exhaustiva de tu apelación, el equipo de administración ha decidido mantener la decisión original del moderador. Esta decisión es final.",
    "repeated_violation": "Hemos revisado tu apelación. La decisión original se mantiene, ya que el contenido infringe claramente las normas comunitarias. Por favor, revisa nuestras políticas para evitar futuras sanciones."
}

# --- CONFIGURACIÓN DE BABEL ---
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
app.config['BABEL_DEFAULT_TIMEZONE'] = 'UTC'
app.config['LANGUAGES'] = {
    'en': 'English',
    'es': 'Español',
}
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'

def select_current_locale():
    # 1. Prioridad: La sesión del usuario. Si ya eligió un idioma, lo respetamos.
    user_lang = session.get('language')
    if user_lang and user_lang in app.config['LANGUAGES'].keys():
        return user_lang
    
    # 2. Si no hay idioma en la sesión (es un nuevo visitante), el idioma por defecto es inglés.
    # Se ignora la cabecera 'Accept-Language' del navegador para asegurar consistencia.
    return app.config['BABEL_DEFAULT_LOCALE'] # Asegúrate que 'en' sea el valor en la config.

babel = Babel(app, locale_selector=select_current_locale)

if app.jinja_env:
    app.jinja_env.globals['format_datetime'] = format_datetime
    app.jinja_env.globals['format_date'] = format_date
    app.jinja_env.globals['format_time'] = format_time
    app.jinja_env.globals['format_timedelta'] = format_timedelta
    app.jinja_env.globals['format_number'] = format_number

# --- FUNCIONES AUXILIARES (MODIFICADAS PARA USAR SQLAlchemy) ---

def create_system_notification(user_id, message, notif_type='system', reference_id=None):
    try:
        notif = Notification(user_id=user_id, mensaje=message, tipo=notif_type, referencia_id=reference_id)
        db.session.add(notif)
    except Exception as e:
        # No hacemos commit aquí, se hará en la ruta que llama a esta función.
        # Pero sí hacemos rollback en caso de error para no dejar la sesión en un estado inconsistente.
        db.session.rollback()
        print(f"!!! ERROR al preparar la notificación del sistema: {e}")

def login_required_api(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify(success=False, error=_('Autenticación requerida. Por favor, inicia sesión.')), 401
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generar_slug(nombre):
    slug = nombre.strip().lower()
    slug = re.sub(r'\s+', '_', slug)
    slug = re.sub(r'[^\w_]', '', slug)
    return slug

def get_blocked_and_blocking_ids(user_id):
    if not user_id:
        return set()
    
    blocked_by_me_q = db.session.query(BlockedUser.blocked_user_id).filter_by(blocker_user_id=user_id)
    blocked_me_q = db.session.query(BlockedUser.blocker_user_id).filter_by(blocked_user_id=user_id)
    
    excluded_ids = {item[0] for item in blocked_by_me_q.all()}
    excluded_ids.update({item[0] for item in blocked_me_q.all()})
    
    return excluded_ids

def regenerar_slugs_si_faltan():
    with app.app_context():
        profiles_to_fix = db.session.query(Profile).filter(or_(Profile.slug == None, Profile.slug == '')).all()
        if not profiles_to_fix:
            print("No hay slugs para regenerar.")
            return

        regenerados_count = 0
        for profile in profiles_to_fix:
            if profile.username:
                new_slug = generar_slug(profile.username)
                existing = db.session.query(Profile).filter(Profile.slug == new_slug, Profile.id != profile.id).first()
                if existing:
                    profile.slug = f"{new_slug}_{profile.user_id}"
                else:
                    profile.slug = new_slug
                regenerados_count += 1
        
        if regenerados_count > 0:
            try:
                db.session.commit()
                print(f"{regenerados_count} slugs regenerados/corregidos.")
            except IntegrityError:
                db.session.rollback()
                print("Error de integridad al regenerar slugs. Se revirtieron los cambios.")

def check_profile_completion(user_id):
    if user_id is None: 
        return False
    profile = db.session.query(Profile).filter_by(user_id=user_id).first()
    return bool(profile and profile.username and profile.username.strip() and profile.slug and profile.slug.strip())

def procesar_menciones_y_notificar(texto, autor_id, id_referencia, tipo_contenido_str):
    if not texto:
        return
    menciones_encontradas = set(re.findall(r'@([a-zA-Z0-9_]+)', texto, flags=re.IGNORECASE))
    if not menciones_encontradas:
        return

    autor_perfil = db.session.query(Profile).filter_by(user_id=autor_id).first()
    if not autor_perfil or not autor_perfil.slug:
        return

    excluded_ids = get_blocked_and_blocking_ids(autor_id)
    autor_slug_enlace = autor_perfil.slug
    autor_nombre_visible = autor_perfil.username or _("Usuario")
    
    for slug_mencionado in menciones_encontradas:
        mencionado = db.session.query(Profile).filter(Profile.slug.ilike(slug_mencionado)).first()
        if mencionado and mencionado.user_id != autor_id and mencionado.user_id not in excluded_ids:
            try:
                enlace_post_url = url_for("ver_publicacion_individual", post_id_vista=int(id_referencia))
            except (ValueError, TypeError):
                enlace_post_url = "#"
            
            autor_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=autor_slug_enlace)}">@{autor_nombre_visible}</a>'
            
            if tipo_contenido_str == "publicación":
                contenido_link_html = f'<a href="{enlace_post_url}">{_("publicación")}</a>'
                mensaje = _('%(autor_link)s te mencionó en una %(contenido_link)s.') % {'autor_link': autor_link_html, 'contenido_link': contenido_link_html}
            else: # comentario
                contenido_link_html = f'<a href="{enlace_post_url}">{_("comentario")}</a>'
                mensaje = _('%(autor_link)s te mencionó en un %(contenido_link)s.') % {'autor_link': autor_link_html, 'contenido_link': contenido_link_html}
            
            create_system_notification(mencionado.user_id, mensaje, 'mencion', id_referencia)
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error al guardar notificaciones de mención: {e}")

def procesar_menciones_para_mostrar(texto):
    if texto is None: 
        return ""
    def reemplazar(match):
        slug_capturado = match.group(1)
        slug_enlace = slug_capturado.lower()
        return f'<a href="{url_for("ver_perfil", slug_perfil=slug_enlace)}">@{slug_capturado}</a>'
    return re.sub(r'@([a-zA-Z0-9_]+)', reemplazar, texto, flags=re.IGNORECASE)

def parse_timestamp(timestamp_str):
    if not timestamp_str:
        return None
    if isinstance(timestamp_str, datetime):
        if timestamp_str.tzinfo:
            return timestamp_str.astimezone(timezone.utc)
        else:
            return timestamp_str.replace(tzinfo=timezone.utc)

    formats_to_try = [
        '%Y-%m-%d %H:%M:%S.%f%z',
        '%Y-%m-%d %H:%M:%S%z',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S'
    ]
    for fmt in formats_to_try:
        try:
            dt_obj = datetime.strptime(str(timestamp_str).split('+')[0], fmt.split('%z')[0].split('.')[0])
            return dt_obj.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
            
    print(f"ADVERTENCIA: No se pudo parsear la cadena de timestamp: '{timestamp_str}' con los formatos probados.")
    return None

def extract_first_url(text):
    if not text:
        return None
    url_pattern = r'https?://[^\s/$.?#].[^\s]*'
    match = re.search(url_pattern, text)
    if match:
        return match.group(0)
    return None

def generate_link_preview(url):
    if not url:
        return None

    preview = { 'url': url, 'title': None, 'description': None, 'image_url': None }
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
    
def log_admin_action(actor_user_id, action_type, target_user_id=None, target_content_id=None, details=None):
    try:
        log = ActionLog(
            actor_user_id=actor_user_id,
            action_type=action_type,
            target_user_id=target_user_id,
            target_content_id=target_content_id,
            details=details
        )
        db.session.add(log)
    except Exception as e:
        db.session.rollback()
        print(f"!!! ERROR al registrar la acción en el log de auditoría: {e}")
        
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash(_('Debes iniciar sesión para ver esta página.'), 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def check_policy_acceptance(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Primero, asegúrate de que el usuario ha iniciado sesión.
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))

        # Evitar bucles de redirección. Permitir el acceso a la página de aceptación y al logout.
        if request.endpoint in ['accept_policies', 'logout', 'static']:
            return f(*args, **kwargs)

        user = db.session.query(User).get(session['user_id'])
        if user and not user.accepted_policies:
            flash(_('Antes de continuar, debes aceptar nuestra Política de Privacidad y Términos de Servicio.'), 'info')
            return redirect(url_for('accept_policies'))
        
        return f(*args, **kwargs)
    return decorated_function

# Inserta este bloque junto a tus otros decoradores (login_required, etc.)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash(_('Debes iniciar sesión para acceder a esta página.'), 'warning')
            return redirect(url_for('index', next=request.url))
        user = User.query.get(session['user_id'])
        if not user or user.role != 'admin':
            flash(_('No tienes permiso para acceder a esta página de administración.'), 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def coordinator_or_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash(_('Debes iniciar sesión para acceder a esta página.'), 'warning')
            return redirect(url_for('index', next=request.url))
        user = User.query.get(session['user_id'])
        if not user or user.role not in ['coordinator', 'admin']:
            flash(_('No tienes los permisos necesarios (se requiere ser al menos coordinador).'), 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def moderator_or_higher_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash(_('Debes iniciar sesión para acceder a esta página.'), 'warning')
            return redirect(url_for('index', next=request.url))
        user = User.query.get(session['user_id'])
        if not user or user.role not in ['moderator', 'coordinator', 'admin']:
            flash(_('No tienes los permisos necesarios (se requiere ser al menos moderador).'), 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def check_sanctions_and_block(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return f(*args, **kwargs)

        user = db.session.query(User).get(session['user_id'])
        if not user:
            session.clear()
            return redirect(url_for('login'))

        now_utc = datetime.now(timezone.utc)

        if user.banned_until and user.banned_until > now_utc:
            session.clear()
            flash(_('Tu cuenta está suspendida y tu sesión ha sido cerrada.'), 'danger')
            return redirect(url_for('login'))

        if user.muted_until and user.muted_until > now_utc:
            flash(_('Tu cuenta está silenciada. No puedes publicar ni comentar temporalmente.'), 'warning')
            return redirect(request.referrer or url_for('feed'))
        
        return f(*args, **kwargs)
    return decorated_function

def check_sanctions_and_block_api(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify(success=False, error=_('Autenticación requerida.')), 401

        user = db.session.query(User).get(session['user_id'])
        if not user:
            session.clear()
            return jsonify(success=False, error=_('Usuario no encontrado.')), 401

        now_utc = datetime.now(timezone.utc)
        
        if user.banned_until and user.banned_until > now_utc:
            session.clear()
            return jsonify(success=False, error=_('Tu cuenta está suspendida.')), 403

        if user.muted_until and user.muted_until > now_utc:
            return jsonify(success=False, error=_('Tu cuenta está silenciada. No puedes realizar esta acción.')), 403
        
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash(_('Debes iniciar sesión para acceder a esta página.'), 'warning')
            return redirect(url_for('login', next=request.url))
        user = db.session.query(User).get(session['user_id'])
        if not user or user.role != 'admin':
            flash(_('No tienes permiso para acceder a esta página de administración.'), 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def coordinator_or_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash(_('Debes iniciar sesión para acceder a esta página.'), 'warning')
            return redirect(url_for('login', next=request.url))
        user = db.session.query(User).get(session['user_id'])
        if not user or user.role not in ['coordinator', 'admin']:
            flash(_('No tienes los permisos necesarios (se requiere ser al menos coordinador) para acceder a esta página.'), 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def moderator_or_higher_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash(_('Debes iniciar sesión para acceder a esta página.'), 'warning')
            return redirect(url_for('login', next=request.url))
        user = db.session.query(User).get(session['user_id'])
        if not user or user.role not in ['moderator', 'coordinator', 'admin']:
            flash(_('No tienes los permisos necesarios (se requiere ser al menos moderador) para acceder a esta página.'), 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.after_request
def add_security_headers(response):
    """Añade cabeceras de seguridad a cada respuesta, incluyendo la CSP."""
    # Permite scripts de nuestro propio dominio ('self'), de Bootstrap ('https://cdn.jsdelivr.net') 
    # y del SDK de Pi ('https://sdk.pi-network.com'). 'unsafe-inline' es necesario
    # para los pequeños scripts que tenemos directamente en las plantillas HTML.
    csp = "script-src 'self' https://cdn.jsdelivr.net https://sdk.pi-network.com 'unsafe-inline';"
    response.headers['Content-Security-Policy'] = csp
    return response

# --- PROCESADOR DE CONTEXTO ---
# EN app.py

@app.context_processor
def inject_global_vars():
    # Variables base disponibles para todos (usuarios invitados y autenticados)
    base_context = {
        'available_languages': app.config['LANGUAGES'],
        'current_locale': str(get_babel_locale()),
        'is_debug_mode': app.debug  # <- AÑADIMOS EL ESTADO DE DEBUG AQUÍ
    }

    user_id = session.get('user_id')
    if not user_id:
        return base_context  # Devolver solo el contexto base para invitados

    user = db.session.query(User).options(joinedload(User.profile)).get(user_id)
    if not user:
        session.clear()
        return base_context

    num_notificaciones_no_leidas = db.session.query(Notification).filter_by(user_id=user_id, leida=False).count()
    
    excluded_ids = get_blocked_and_blocking_ids(user_id)
    unread_messages_query = db.session.query(func.count(Message.id))\
        .join(ConversationParticipant, Message.conversation_id == ConversationParticipant.conversation_id)\
        .filter(
            ConversationParticipant.user_id == user_id,
            Message.sender_id != user_id,
            Message.is_read == False
        )
    if excluded_ids:
        unread_messages_query = unread_messages_query.filter(Message.sender_id.notin_(excluded_ids))
    
    num_mensajes_no_leidos = unread_messages_query.scalar() or 0

    # Añadir las variables específicas del usuario al diccionario base
    base_context.update(
        foto_usuario_actual=user.profile.photo if user.profile else None,
        notificaciones_no_leidas_count=num_notificaciones_no_leidas,
        unread_messages_count=num_mensajes_no_leidos,
        display_username_session=user.profile.username if user.profile and user.profile.username else user.username,
        now=datetime.utcnow,
        current_user_role=user.role
    )
    
    return base_context

# --- RUTAS ---
@app.route('/language/<lang>')
def set_language(lang=None):
    if lang and lang in app.config['LANGUAGES']:
        session['language'] = lang
    return redirect(request.args.get('next') or request.referrer or url_for('index'))

@app.route('/')
def index():
    user_id = session.get('user_id')
    perfil_completo = check_profile_completion(user_id) if user_id else False
    return render_template('index.html', perfil_completo=perfil_completo)


@app.route('/logout')
@login_required
def logout():
    session.clear()
    flash(_('Has cerrado sesión.'), 'info')
    return redirect(url_for('index'))

@app.route('/api/pi/auth/complete', methods=['POST'])
def pi_auth_complete():
    auth_result = request.json
    if not auth_result or 'accessToken' not in auth_result:
        return jsonify(success=False, error=_('Autorización de Pi inválida.'))

    PI_API_KEY = os.environ.get('PI_API_KEY')
    if not PI_API_KEY:
        return jsonify(success=False, error=_('Clave API de Pi no configurada en el servidor.'))

    try:
        response = requests.post(
            'https://api.pi.network/v2/auth/serverside-verification',
            json={'accessToken': auth_result['accessToken']},
            headers={'Authorization': f'Key {PI_API_KEY}'}
        )
        response.raise_for_status()
        pi_user_data = response.json()
    except requests.RequestException as e:
        return jsonify(success=False, error=_('No se pudo verificar la sesión con Pi.'))

    pi_uid = pi_user_data.get('uid')
    pi_username = pi_user_data.get('username')
    if not pi_uid:
        return jsonify(success=False, error=_('Respuesta de Pi inválida.'))

    user = User.query.filter_by(pi_uid=pi_uid).first()
    if not user:
        # Creamos el usuario y su perfil asociado
        user = User(username=pi_username, password='no-password', pi_uid=pi_uid, accepted_policies=False) # Inicia sin aceptar políticas
        db.session.add(user)
        db.session.flush() # Para obtener el user.id para el perfil
        
        profile = Profile(user_id=user.id)
        db.session.add(profile)
        db.session.commit()

    # Inicia sesión
    session['user_id'] = user.id
    session['pi_uid'] = pi_uid

    # --- CAMBIO IMPORTANTE ---
    # En lugar de un simple success, devolvemos la URL a la que redirigir.
    # El feed es un buen destino después de iniciar sesión.
    redirect_url = url_for('feed')
    return jsonify(success=True, redirect_url=redirect_url)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
@check_policy_acceptance
def profile():
    user_id_actual = session['user_id']
    profile = db.session.query(Profile).filter_by(user_id=user_id_actual).one_or_none()
    if not profile:
        # Esto puede pasar si un usuario se borra pero la sesión persiste.
        flash(_("No se encontró tu perfil, por favor contacta con soporte."), 'danger')
        return redirect(url_for('logout'))

    if request.method == 'POST':
        nuevo_username_perfil = request.form.get('username', '').strip()
        nueva_bio = request.form.get('bio', '').strip()
        archivo_foto = request.files.get('photo')

        if not nuevo_username_perfil:
            flash(_('El nombre de usuario público no puede estar vacío.'), 'danger')
        else:
            nuevo_slug = generar_slug(nuevo_username_perfil)
            if not nuevo_slug:
                flash(_('El nombre de usuario público debe contener caracteres alfanuméricos válidos.'), 'danger')
                return render_template('profile.html', profile=profile)

            existing_profile = db.session.query(Profile).filter(
                Profile.user_id != user_id_actual,
                or_(Profile.username.ilike(nuevo_username_perfil), Profile.slug.ilike(nuevo_slug))
            ).first()

            if existing_profile:
                flash(_("Ese nombre de usuario público o slug ya está en uso por otra persona."), 'danger')
            else:
                profile.username = nuevo_username_perfil
                profile.slug = nuevo_slug
                profile.bio = nueva_bio
                
                if archivo_foto and allowed_file(archivo_foto.filename):
                    nombre_seguro_foto = secure_filename(archivo_foto.filename)
                    foto_actual_filename = f"user_{user_id_actual}_{int(time.time())}_{nombre_seguro_foto}"
                    try:
                        archivo_foto.save(os.path.join(app.config['UPLOAD_FOLDER'], foto_actual_filename))
                        profile.photo = foto_actual_filename
                    except Exception as e:
                        flash(_("Error al guardar la foto: %(error)s", error=str(e)), "danger")
                
                try:
                    db.session.commit()
                    session['display_username'] = profile.username
                    flash(_("Perfil actualizado correctamente."), 'success')
                    return redirect(url_for('profile'))
                except IntegrityError:
                    db.session.rollback()
                    flash(_("Error de integridad. Es posible que el nombre de usuario o slug ya exista."), 'danger')
                except Exception as e:
                    db.session.rollback()
                    flash(_("Ocurrió un error inesperado al actualizar el perfil."), 'danger')
                    print(f"Error en actualización de perfil: {e}")

    datos_perfil = {
        'username': profile.username,
        'bio': profile.bio,
        'photo': profile.photo,
        'slug': profile.slug
    }
    if request.method == 'GET' and not (profile.username and profile.username.strip()):
        flash(_("Por favor, completa tu nombre de usuario público en el perfil."), "info")
        
    return render_template('profile.html', profile=datos_perfil)


@app.route('/feed')
@login_required
@check_policy_acceptance
def feed():
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Debes completar tu perfil para ver el feed y publicar.'), 'warning')
        return redirect(url_for('profile'))

    all_sections = db.session.query(Section).order_by(Section.name).all()
    
    # La paginación y carga de posts se maneja principalmente por la ruta API /api/feed
    # Esta ruta solo renderiza el esqueleto de la página. La carga inicial de posts se omite
    # para favorecer la carga dinámica con JavaScript.
    return render_template('feed.html', 
                           posts=[], # Los posts se cargarán dinámicamente
                           sections=all_sections,
                           POSTS_PER_PAGE=POSTS_PER_PAGE)


@app.route('/post', methods=['POST'])
@login_required
@check_sanctions_and_block
@check_policy_acceptance
def post():
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil antes de publicar.'), 'warning')
        return redirect(url_for('profile'))

    contenido_post = request.form.get('content', '').strip()
    archivo_imagen = request.files.get('post_image')
    section_id_str = request.form.get('section_id')
    
    if not contenido_post and not archivo_imagen:
        flash(_('La publicación no puede estar completamente vacía. Añade texto o una imagen.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    nombre_archivo_imagen = None
    if archivo_imagen and allowed_file(archivo_imagen.filename):
        timestamp_actual_img = datetime.now().strftime("%Y%m%d%H%M%S%f")
        nombre_seguro_original = secure_filename(archivo_imagen.filename)
        nombre_archivo_imagen = f"post_{user_id_actual}_{timestamp_actual_img}_{nombre_seguro_original}"
        ruta_guardado = os.path.join(POST_IMAGES_FOLDER, nombre_archivo_imagen)
        try:
            archivo_imagen.save(ruta_guardado)
        except Exception as e:
            flash(_("Error al guardar la imagen de la publicación: %(error)s", error=str(e)), "danger")
            nombre_archivo_imagen = None
    
    preview_data = {}
    first_url_found = extract_first_url(contenido_post)
    if first_url_found:
        link_preview_data = generate_link_preview(first_url_found)
        if link_preview_data and (link_preview_data.get('title') or link_preview_data.get('description')):
            preview_data = {
                'preview_url': link_preview_data.get('url'),
                'preview_title': link_preview_data.get('title'),
                'preview_description': link_preview_data.get('description'),
                'preview_image_url': link_preview_data.get('image_url'),
            }

    try:
        new_post = Post(
            user_id=user_id_actual,
            content=contenido_post,
            image_filename=nombre_archivo_imagen,
            section_id=int(section_id_str) if section_id_str and section_id_str.isdigit() else None,
            **preview_data
        )
        db.session.add(new_post)
        db.session.flush() 
        
        if contenido_post:
            procesar_menciones_y_notificar(contenido_post, user_id_actual, new_post.id, "publicación")
        
        db.session.commit()
        flash(_('Publicación creada.'), 'success')
    except Exception as e:
        db.session.rollback()
        flash(_('Ocurrió un error al crear la publicación.'), 'danger')
        print(f"Error al crear post: {e}")

    return redirect(request.referrer or url_for('feed'))

@app.route('/post/<int:post_id>/delete', methods=['POST'])
@login_required
@check_policy_acceptance
def delete_post(post_id):
    user_id_actual = session['user_id']
    post = db.session.query(Post).get(post_id)
    if not post:
        flash(_('Publicación no encontrada.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    current_user = db.session.query(User).get(user_id_actual)

    if post.user_id != user_id_actual and current_user.role not in ['moderator', 'coordinator', 'admin']:
        flash(_('No tienes permiso para eliminar esta publicación.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    post.is_visible = False
    
    if post.user_id != user_id_actual:
        log_details = f"Ocultó un post (ID: {post.id}, contenido: '{post.content[:100]}...') del usuario con ID {post.user_id}."
        log_admin_action(user_id_actual, 'POST_HIDE_BY_MOD', target_user_id=post.user_id, target_content_id=post.id, details=log_details)
    
    db.session.commit()
    flash(_('Publicación eliminada correctamente.'), 'success')
    
    if request.referrer and '/admin/posts' in request.referrer:
         return redirect(url_for('admin_list_posts'))
    return redirect(request.referrer or url_for('feed'))


@app.route('/react_to_post/<int:post_id>', methods=['POST'])
@check_sanctions_and_block_api
def react_to_post(post_id):
    user_id_actual = session['user_id']
    reaction_type = request.form.get('reaction_type')
    allowed_reactions = ['like', 'love', 'haha', 'wow', 'sad', 'angry']

    if not reaction_type or reaction_type not in allowed_reactions:
        return jsonify(success=False, error='invalid_reaction_type'), 400

    post = db.session.query(Post).get(post_id)
    if not post or not post.is_visible:
        return jsonify(success=False, error='post_not_found'), 404
        
    existing_reaction = db.session.query(PostReaction).filter_by(post_id=post_id, user_id=user_id_actual).first()

    action_taken = ''
    if existing_reaction:
        if existing_reaction.reaction_type == reaction_type:
            db.session.delete(existing_reaction)
            action_taken = 'removed'
        else:
            existing_reaction.reaction_type = reaction_type
            action_taken = 'updated'
    else:
        new_reaction = PostReaction(post_id=post_id, user_id=user_id_actual, reaction_type=reaction_type)
        db.session.add(new_reaction)
        action_taken = 'created'
    
    db.session.commit()
    total_reactions = db.session.query(PostReaction).filter_by(post_id=post_id).count()

    return jsonify(
        success=True, 
        action=action_taken, 
        new_total=total_reactions,
        reaction_type=reaction_type if action_taken != 'removed' else None
    )

@app.route('/comment/<int:post_id>', methods=['POST'])
@login_required
@check_sanctions_and_block
@check_policy_acceptance
def comment(post_id):
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil antes de comentar.'), 'warning')
        return redirect(url_for('profile'))

    contenido_comentario = request.form.get('content', '').strip()
    parent_comment_id_str = request.form.get('parent_comment_id')
    parent_comment_id = int(parent_comment_id_str) if parent_comment_id_str and parent_comment_id_str.isdigit() else None

    if not contenido_comentario:
        flash(_('El comentario no puede estar vacío.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    post = db.session.query(Post).get(post_id)
    if not post or not post.is_visible:
        flash(_('La publicación a la que intentas responder no existe.'), 'danger')
        return redirect(url_for('feed'))
    
    excluded_ids = get_blocked_and_blocking_ids(user_id_actual)
    if post.user_id in excluded_ids:
        flash(_('No puedes interactuar con este usuario o publicación.'), 'danger')
        return redirect(url_for('feed'))

    try:
        new_comment = Comment(
            post_id=post_id, 
            user_id=user_id_actual, 
            content=contenido_comentario, 
            parent_comment_id=parent_comment_id
        )
        db.session.add(new_comment)
        db.session.flush()

        commenter_profile = db.session.query(Profile).filter_by(user_id=user_id_actual).first()
        commenter_slug = commenter_profile.slug if commenter_profile else "#"
        commenter_name = commenter_profile.username if commenter_profile else _("Usuario")
        commenter_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=commenter_slug)}">@{commenter_name}</a>'
        post_link_html = f'<a href="{url_for("ver_publicacion_individual", post_id_vista=post_id)}#comment-{new_comment.id}">{_("publicación")}</a>'

        if parent_comment_id:
            parent_comment = db.session.query(Comment).get(parent_comment_id)
            if parent_comment and parent_comment.user_id != user_id_actual and parent_comment.user_id not in excluded_ids:
                mensaje_notif = _('%(commenter_link)s ha respondido a tu comentario en una %(post_link)s.') % {'commenter_link': commenter_link_html, 'post_link': post_link_html}
                create_system_notification(parent_comment.user_id, mensaje_notif, 'respuesta_comentario', post_id)
        elif post.user_id != user_id_actual:
            mensaje_notif = _('%(commenter_link)s ha comentado en tu %(post_link)s.') % {'commenter_link': commenter_link_html, 'post_link': post_link_html}
            create_system_notification(post.user_id, mensaje_notif, 'nuevo_comentario', post_id)

        if contenido_comentario:
            # Llamamos a procesar menciones después de las notificaciones principales
            procesar_menciones_y_notificar(contenido_comentario, user_id_actual, post_id, "comentario")
        
        db.session.commit()
        flash(_('Comentario añadido.'), 'success')
    except Exception as e:
        db.session.rollback()
        flash(_('Ocurrió un error al añadir el comentario.'), 'danger')
        print(f"Error al añadir comentario: {e}")
    
    if request.referrer and f'/post/{post_id}' in request.referrer:
        return redirect(url_for('ver_publicacion_individual', post_id_vista=post_id, _anchor=f'comment-{new_comment.id}'))
    return redirect(request.referrer or url_for('feed'))

@app.route('/comment/<int:comment_id>/delete', methods=['POST'])
@login_required
def delete_comment(comment_id):
    user_id_actual = session['user_id']
    comment = db.session.query(Comment).get(comment_id)
    if not comment:
        flash(_('Comentario no encontrado.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    current_user = db.session.query(User).get(user_id_actual)
    if not current_user:
        return redirect(url_for('logout'))

    if comment.user_id != user_id_actual and current_user.role not in ['moderator', 'coordinator', 'admin']:
        flash(_('No tienes permiso para eliminar este comentario.'), 'danger')
        return redirect(url_for('feed'))

    comment.is_visible = False
    post_id_original = comment.post_id

    if comment.user_id != user_id_actual:
        log_details = f"Ocultó un comentario (ID: {comment.id}, contenido: '{comment.content[:100]}...') del usuario con ID {comment.user_id}."
        log_admin_action(user_id_actual, 'COMMENT_HIDE_BY_MOD', target_user_id=comment.user_id, target_content_id=comment.id, details=log_details)

    db.session.commit()
    flash(_('Comentario eliminado correctamente.'), 'success')

    if request.referrer and '/admin/comments' in request.referrer:
         return redirect(url_for('admin_list_comments'))
    elif post_id_original:
        return redirect(url_for('ver_publicacion_individual', post_id_vista=post_id_original))
    else:
        return redirect(url_for('feed'))

@app.route('/react_to_comment/<int:comment_id>', methods=['POST'])
@check_sanctions_and_block_api
def react_to_comment(comment_id):
    user_id_actual = session['user_id']
    reaction_type = request.form.get('reaction_type')
    allowed_reactions = ['like', 'love', 'haha', 'wow', 'sad', 'angry']

    if not reaction_type or reaction_type not in allowed_reactions:
        return jsonify(success=False, error='invalid_reaction_type'), 400

    comment = db.session.query(Comment).get(comment_id)
    if not comment or not comment.is_visible:
        return jsonify(success=False, error='comment_not_found'), 404

    existing_reaction = db.session.query(CommentReaction).filter_by(comment_id=comment_id, user_id=user_id_actual).first()
    
    action_taken = ''
    if existing_reaction:
        if existing_reaction.reaction_type == reaction_type:
            db.session.delete(existing_reaction)
            action_taken = 'removed'
        else:
            existing_reaction.reaction_type = reaction_type
            action_taken = 'updated'
    else:
        new_reaction = CommentReaction(comment_id=comment_id, user_id=user_id_actual, reaction_type=reaction_type)
        db.session.add(new_reaction)
        action_taken = 'created'
            
    db.session.commit()
    total_reactions = db.session.query(CommentReaction).filter_by(comment_id=comment_id).count()

    return jsonify(
        success=True,
        action=action_taken,
        new_total=total_reactions,
        reaction_type=reaction_type if action_taken != 'removed' else None
    )

@app.route('/post/<int:post_id>/share', methods=['POST'])
@login_required
@check_sanctions_and_block
@check_policy_acceptance
def share_post(post_id):
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil antes de compartir publicaciones.'), 'warning')
        return redirect(url_for('profile'))

    quote_content = request.form.get('quote_content', '').strip()
    post_original = db.session.query(Post).get(post_id)

    if not post_original or not post_original.is_visible:
        flash(_('La publicación que intentas compartir no existe.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    excluded_ids = get_blocked_and_blocking_ids(user_id_actual)
    if post_original.user_id in excluded_ids:
        flash(_('No puedes interactuar con esta publicación.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    try:
        new_share = SharedPost(
            user_id=user_id_actual, 
            original_post_id=post_id, 
            quote_content=quote_content if quote_content else None
        )
        db.session.add(new_share)
        
        if post_original.user_id != user_id_actual:
            sharer_profile = db.session.query(Profile).filter_by(user_id=user_id_actual).first()
            sharer_username = sharer_profile.username if sharer_profile else _("Alguien")
            sharer_slug = sharer_profile.slug if sharer_profile else "#"
            sharer_link = f'<a href="{url_for("ver_perfil", slug_perfil=sharer_slug)}">@{sharer_username}</a>'
            post_link = f'<a href="{url_for("ver_publicacion_individual", post_id_vista=post_id)}">{_("publicación")}</a>'
            
            if quote_content:
                mensaje = _('%(sharer_link)s ha citado tu %(post_link)s.') % {'sharer_link': sharer_link, 'post_link': post_link}
                tipo_notif = 'share_post_with_quote'
            else:
                mensaje = _('%(sharer_link)s ha compartido tu %(post_link)s.') % {'sharer_link': sharer_link, 'post_link': post_link}
                tipo_notif = 'share_post'
            
            create_system_notification(post_original.user_id, mensaje, tipo_notif, post_id)
        
        db.session.commit()
        flash(_('Publicación compartida correctamente.'), 'success')
    except IntegrityError:
        db.session.rollback()
        flash(_('Ya has compartido esta publicación anteriormente.'), 'info')
    except Exception as e:
        db.session.rollback()
        flash(_('Ha ocurrido un error al intentar compartir la publicación.'), 'danger')
        print(f"Error al compartir post: {e}")

    return redirect(request.referrer or url_for('feed'))

# Inserta este bloque después de la función share_post

# --- RUTAS DE GESTIÓN DE CONTACTOS ---

@app.route('/contactos')
@login_required
@check_policy_acceptance
def contactos():
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil para ver tus contactos.'), 'warning')
        return redirect(url_for('profile'))

    search_term = request.args.get('q', '').strip()
    
    # Subconsulta para obtener los IDs de los contactos aceptados
    sent_req = db.session.query(Contact.receptor_id).filter_by(solicitante_id=user_id_actual, estado='aceptado')
    received_req = db.session.query(Contact.solicitante_id).filter_by(receptor_id=user_id_actual, estado='aceptado')
    contact_ids = [item[0] for item in sent_req.union(received_req).all()]

    # Obtener los perfiles de los contactos
    contactos_query = Profile.query.filter(Profile.user_id.in_(contact_ids))
    if search_term:
        contactos_query = contactos_query.filter(or_(Profile.username.ilike(f'%{search_term}%'), Profile.slug.ilike(f'%{search_term}%')))
    
    lista_de_contactos = contactos_query.order_by(Profile.username.asc()).all()

    # Obtener los perfiles de los usuarios bloqueados por el usuario actual
    lista_de_bloqueados = Profile.query.join(User).join(BlockedUser, User.id == BlockedUser.blocked_user_id)\
        .filter(BlockedUser.blocker_user_id == user_id_actual).order_by(Profile.username.asc()).all()
    
    return render_template('contactos.html',
                           contactos=lista_de_contactos,
                           bloqueados=lista_de_bloqueados,
                           search_term=search_term)
    
# Inserta este bloque después de las rutas de Contactos

# --- RUTAS DE MENSAJERÍA ---

@app.route('/mensajes')
@login_required
@check_policy_acceptance
def mensajes():
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil para usar la mensajería.'), 'warning')
        return redirect(url_for('profile'))

    excluded_ids = get_blocked_and_blocking_ids(user_id_actual)

    # Subconsulta para encontrar el último mensaje de cada conversación
    last_message_subquery = db.session.query(
        Message.conversation_id,
        func.max(Message.timestamp).label('last_message_time')
    ).group_by(Message.conversation_id).subquery()

    # Consulta principal para obtener las conversaciones del usuario actual
    user_conversations = db.session.query(
        Conversation.id.label('conversation_id'),
        User.id.label('other_user_id'),
        Profile.username.label('other_username'),
        Profile.photo.label('other_photo'),
        Profile.slug.label('other_slug'),
        last_message_subquery.c.last_message_time
    ).join(
        Conversation.participants
    ).join(
        User, User.id == ConversationParticipant.user_id
    ).filter(
        ConversationParticipant.conversation_id.in_(
            db.session.query(ConversationParticipant.conversation_id).filter_by(user_id=user_id_actual)
        ),
        User.id != user_id_actual, # El otro participante
        User.id.notin_(excluded_ids) # No bloqueado
    ).join(
        last_message_subquery, last_message_subquery.c.conversation_id == Conversation.id
    ).order_by(desc(last_message_subquery.c.last_message_time)).all()

    # Procesar resultados para la plantilla
    conversations_list = []
    for conv_data in user_conversations:
        last_msg = Message.query.filter(
            Message.conversation_id == conv_data.conversation_id,
            Message.timestamp == conv_data.last_message_time
        ).first()

        unread_count = db.session.query(func.count(Message.id)).filter(
            Message.conversation_id == conv_data.conversation_id,
            Message.sender_id != user_id_actual,
            Message.is_read == False
        ).scalar()

        conversations_list.append({
            'conversation_id': conv_data.conversation_id,
            'other_user': {
                'id': conv_data.other_user_id,
                'username': conv_data.other_username or 'Usuario',
                'photo': conv_data.other_photo,
                'slug': conv_data.other_slug or '#'
            },
            'last_message': last_msg,
            'unread_count': unread_count
        })

    return render_template('mensajes.html', conversations=conversations_list)


@app.route('/mensajes/<int:conversation_id>')
@login_required
@check_policy_acceptance
def ver_conversacion(conversation_id):
    user_id_actual = session['user_id']
    
    participant = ConversationParticipant.query.filter_by(conversation_id=conversation_id, user_id=user_id_actual).first_or_404()

    other_participant = ConversationParticipant.query.filter(
        ConversationParticipant.conversation_id == conversation_id,
        ConversationParticipant.user_id != user_id_actual
    ).first()

    other_user_data = {'username': _('Usuario'), 'photo': None, 'slug': '#'}
    if other_participant:
        excluded_ids = get_blocked_and_blocking_ids(user_id_actual)
        if other_participant.user_id in excluded_ids:
            flash(_('No puedes ver esta conversación debido a la configuración de bloqueo.'), 'danger')
            return redirect(url_for('mensajes'))
        
        other_profile = Profile.query.filter_by(user_id=other_participant.user_id).first()
        other_user_data['username'] = other_profile.username if other_profile else other_participant.user.username
        other_user_data['photo'] = other_profile.photo if other_profile else None
        other_user_data['slug'] = other_profile.slug if other_profile else '#'
        
    Message.query.filter(
        Message.conversation_id == conversation_id,
        Message.sender_id != user_id_actual
    ).update({'is_read': True})
    db.session.commit()

    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp.asc()).all()

    return render_template('conversacion.html',
                           conversation_id=conversation_id,
                           messages=messages,
                           other_user=other_user_data,
                           current_user_id=user_id_actual)

@app.route('/mensajes/iniciar/<int:receptor_id>', methods=['POST'])
@login_required
def iniciar_conversacion(receptor_id):
    user_id_actual = session['user_id']
    
    # Comprobar que son contactos
    are_contacts = Contact.query.filter(
        Contact.estado == 'aceptado',
        or_(
            and_(Contact.solicitante_id == user_id_actual, Contact.receptor_id == receptor_id),
            and_(Contact.solicitante_id == receptor_id, Contact.receptor_id == user_id_actual)
        )
    ).first()
    
    if not are_contacts:
        flash(_('Solo puedes enviar mensajes a tus contactos.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    # Buscar conversación existente
    existing_conversation = Conversation.query.join(Conversation.participants, aliased=True).filter(
        ConversationParticipant.user_id == user_id_actual
    ).join(Conversation.participants, aliased=True).filter(
        ConversationParticipant.user_id == receptor_id
    ).first()

    if existing_conversation:
        return redirect(url_for('ver_conversacion', conversation_id=existing_conversation.id))
    else:
        # Crear nueva conversación
        new_conv = Conversation()
        db.session.add(new_conv)
        db.session.flush() # Para obtener el ID
        
        p1 = ConversationParticipant(conversation_id=new_conv.id, user_id=user_id_actual)
        p2 = ConversationParticipant(conversation_id=new_conv.id, user_id=receptor_id)
        db.session.add_all([p1, p2])
        
        db.session.commit()
        return redirect(url_for('ver_conversacion', conversation_id=new_conv.id))

@app.route('/enviar_solicitud/<int:id_receptor>', methods=['POST'])
@login_required
@check_policy_acceptance
def enviar_solicitud(id_receptor):
    id_solicitante = session['user_id']
    if not check_profile_completion(id_solicitante):
        flash(_('Por favor, completa tu perfil para enviar solicitudes.'), 'warning')
        return redirect(url_for('profile'))

    if id_solicitante == id_receptor:
        flash(_('No puedes enviarte una solicitud a ti mismo.'), 'warning')
        return redirect(request.referrer or url_for('feed'))

    excluded_ids = get_blocked_and_blocking_ids(id_solicitante)
    if id_receptor in excluded_ids:
        flash(_('No puedes interactuar con este usuario.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    existing_contact = Contact.query.filter(
        or_(
            and_(Contact.solicitante_id == id_solicitante, Contact.receptor_id == id_receptor),
            and_(Contact.solicitante_id == id_receptor, Contact.receptor_id == id_solicitante)
        )
    ).first()
    
    if existing_contact:
        flash(_('Ya existe una solicitud o conexión con este usuario.'), 'info')
    else:
        new_contact = Contact(solicitante_id=id_solicitante, receptor_id=id_receptor, estado='pendiente')
        db.session.add(new_contact)

        solicitante_perfil = Profile.query.filter_by(user_id=id_solicitante).first()
        if solicitante_perfil and solicitante_perfil.slug:
            solicitante_nombre = solicitante_perfil.username or _("Usuario")
            solicitante_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=solicitante_perfil.slug)}">@{solicitante_nombre}</a>'
            mensaje_notif = _('%(solicitante_link)s te ha enviado una solicitud de contacto.') % {'solicitante_link': solicitante_link_html}
            create_system_notification(id_receptor, mensaje_notif, 'solicitud_contacto', id_solicitante)
        
        db.session.commit()
        flash(_('Solicitud de contacto enviada.'), 'success')
        
    return redirect(request.referrer or url_for('feed'))

@app.route('/aceptar_solicitud/<int:id_solicitante>', methods=['POST'])
@login_required
def aceptar_solicitud(id_solicitante):
    id_receptor = session['user_id']
    
    contact_request = Contact.query.filter_by(solicitante_id=id_solicitante, receptor_id=id_receptor, estado='pendiente').first()

    if contact_request:
        contact_request.estado = 'aceptado'
        
        receptor_perfil = Profile.query.filter_by(user_id=id_receptor).first()
        if receptor_perfil and receptor_perfil.slug:
            receptor_nombre = receptor_perfil.username or _("Usuario")
            receptor_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=receptor_perfil.slug)}">@{receptor_nombre}</a>'
            mensaje_notif = _('%(receptor_link)s aceptó tu solicitud de contacto.') % {'receptor_link': receptor_link_html}
            create_system_notification(id_solicitante, mensaje_notif, 'solicitud_aceptada', id_receptor)

        db.session.commit()
        flash(_('Solicitud de contacto aceptada.'), 'success')
    else:
        flash(_('No se pudo aceptar la solicitud. Quizás ya no estaba pendiente.'), 'warning')
        
    return redirect(url_for('notificaciones'))

@app.route('/rechazar_solicitud/<int:id_solicitante>', methods=['POST'])
@login_required
def rechazar_solicitud(id_solicitante):
    id_receptor = session['user_id']
    contact_request = Contact.query.filter_by(solicitante_id=id_solicitante, receptor_id=id_receptor, estado='pendiente').first()

    if contact_request:
        db.session.delete(contact_request)
        db.session.commit()
        flash(_('Solicitud de contacto rechazada.'), 'info')
    else:
        flash(_('No se pudo rechazar la solicitud.'), 'warning')
        
    return redirect(url_for('notificaciones'))

@app.route('/eliminar_contacto/<int:id_otro_usuario>', methods=['POST'])
@login_required
def eliminar_contacto(id_otro_usuario):
    user_id_actual = session['user_id']
    contact = Contact.query.filter(
        or_(
            and_(Contact.solicitante_id == user_id_actual, Contact.receptor_id == id_otro_usuario),
            and_(Contact.solicitante_id == id_otro_usuario, Contact.receptor_id == user_id_actual)
        )
    ).first()
    
    if contact:
        db.session.delete(contact)
        db.session.commit()
        flash(_('Contacto eliminado.'), 'success')
    else:
        flash(_('No se encontró una relación de contacto para eliminar.'), 'info')
        
    return redirect(request.referrer or url_for('contactos'))

# --- RUTAS DE MENSAJERÍA ---

@app.route('/mensajes')
@login_required
@check_policy_acceptance
def mensajes():
    user_id_actual = session['user_id']
    if not check_profile_completion(user_id_actual):
        flash(_('Por favor, completa tu perfil para usar la mensajería.'), 'warning')
        return redirect(url_for('profile'))

    excluded_ids = get_blocked_and_blocking_ids(user_id_actual)
    
    # Subconsulta para encontrar el último mensaje de cada conversación
    last_message_subq = db.session.query(
        Message.conversation_id,
        func.max(Message.timestamp).label('last_timestamp')
    ).group_by(Message.conversation_id).subquery()

    # Consulta principal
    conversations = db.session.query(
        Conversation,
        User,
        Profile,
        Message
    ).join(
        Conversation.participants
    ).join(
        User, User.id == ConversationParticipant.user_id
    ).outerjoin(
        Profile, Profile.user_id == User.id
    ).join(
        last_message_subq, last_message_subq.c.conversation_id == Conversation.id
    ).join(
        Message, and_(Message.conversation_id == last_message_subq.c.conversation_id, Message.timestamp == last_message_subq.c.last_timestamp)
    ).filter(
        ConversationParticipant.user_id == user_id_actual, # Participante actual
        User.id != user_id_actual, # El otro participante
        User.id.notin_(excluded_ids) # No bloqueado
    ).order_by(desc(last_message_subq.c.last_timestamp)).all()

    # Procesar resultados para la plantilla
    conversations_list = []
    for conv, other_user, other_profile, last_msg in conversations:
        unread_count = db.session.query(Message).filter(
            Message.conversation_id == conv.id,
            Message.sender_id != user_id_actual,
            Message.is_read == False
        ).count()

        conversations_list.append({
            'conversation_id': conv.id,
            'other_user': {
                'id': other_user.id,
                'username': other_profile.username if other_profile else other_user.username,
                'photo': other_profile.photo if other_profile else None,
                'slug': other_profile.slug if other_profile else '#'
            },
            'last_message': last_msg,
            'unread_count': unread_count
        })

    return render_template('mensajes.html', conversations=conversations_list)


@app.route('/mensajes/<int:conversation_id>')
@login_required
@check_policy_acceptance
def ver_conversacion(conversation_id):
    user_id_actual = session['user_id']
    
    # Verificar que el usuario es participante
    participant = ConversationParticipant.query.filter_by(conversation_id=conversation_id, user_id=user_id_actual).first()
    if not participant:
        flash(_('No tienes permiso para ver esta conversación.'), 'danger')
        return redirect(url_for('mensajes'))

    # Obtener el otro participante
    other_participant = ConversationParticipant.query.filter(
        ConversationParticipant.conversation_id == conversation_id,
        ConversationParticipant.user_id != user_id_actual
    ).first()

    other_user_data = None
    if other_participant:
        other_user = User.query.get(other_participant.user_id)
        other_profile = Profile.query.filter_by(user_id=other_user.id).first()
        other_user_data = {
            'username': other_profile.username if other_profile else other_user.username,
            'photo': other_profile.photo if other_profile else None,
            'slug': other_profile.slug if other_profile else '#'
        }

    # Marcar mensajes como leídos
    Message.query.filter(
        Message.conversation_id == conversation_id,
        Message.sender_id != user_id_actual,
        Message.is_read == False
    ).update({'is_read': True})
    db.session.commit()

    # Obtener todos los mensajes de la conversación
    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp.asc()).all()

    return render_template('conversacion.html',
                           conversation_id=conversation_id,
                           messages=messages,
                           other_user=other_user_data)

@app.route('/enviar_solicitud/<int:id_receptor>', methods=['POST'])
@login_required
@check_policy_acceptance
def enviar_solicitud(id_receptor):
    id_solicitante = session['user_id']
    if not check_profile_completion(id_solicitante):
        flash(_('Por favor, completa tu perfil antes de enviar solicitudes.'), 'warning')
        return redirect(url_for('profile'))

    if id_solicitante == id_receptor:
        flash(_('No puedes enviarte una solicitud a ti mismo.'), 'warning')
        return redirect(request.referrer or url_for('feed'))

    excluded_ids = get_blocked_and_blocking_ids(id_solicitante)
    if id_receptor in excluded_ids:
        flash(_('No puedes interactuar con este usuario.'), 'danger')
        return redirect(request.referrer or url_for('feed'))

    existing_contact = Contact.query.filter(
        or_(
            and_(Contact.solicitante_id == id_solicitante, Contact.receptor_id == id_receptor),
            and_(Contact.solicitante_id == id_receptor, Contact.receptor_id == id_solicitante)
        )
    ).first()
    
    if existing_contact:
        flash(_('Ya existe una solicitud o conexión con este usuario.'), 'info')
    else:
        new_contact = Contact(solicitante_id=id_solicitante, receptor_id=id_receptor, estado='pendiente')
        db.session.add(new_contact)

        solicitante_perfil = Profile.query.filter_by(user_id=id_solicitante).first()
        if solicitante_perfil:
            solicitante_nombre = solicitante_perfil.username or _("Usuario")
            solicitante_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=solicitante_perfil.slug)}">@{solicitante_nombre}</a>'
            mensaje_notif = _('%(solicitante_link)s te ha enviado una solicitud de contacto.') % {'solicitante_link': solicitante_link_html}
            create_system_notification(id_receptor, mensaje_notif, 'solicitud_contacto', id_solicitante)
        
        db.session.commit()
        flash(_('Solicitud de contacto enviada.'), 'success')
        
    return redirect(request.referrer or url_for('feed'))

@app.route('/aceptar_solicitud/<int:id_solicitante>', methods=['POST'])
@login_required
def aceptar_solicitud(id_solicitante):
    id_receptor = session['user_id']
    
    contact_request = Contact.query.filter_by(solicitante_id=id_solicitante, receptor_id=id_receptor, estado='pendiente').first()

    if contact_request:
        contact_request.estado = 'aceptado'
        
        receptor_perfil = Profile.query.filter_by(user_id=id_receptor).first()
        if receptor_perfil:
            receptor_nombre = receptor_perfil.username or _("Usuario")
            receptor_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=receptor_perfil.slug)}">@{receptor_nombre}</a>'
            mensaje_notif = _('%(receptor_link)s aceptó tu solicitud de contacto.') % {'receptor_link': receptor_link_html}
            create_system_notification(id_solicitante, mensaje_notif, 'solicitud_aceptada', id_receptor)

        db.session.commit()
        flash(_('Solicitud de contacto aceptada.'), 'success')
    else:
        flash(_('No se pudo aceptar la solicitud.'), 'warning')
        
    return redirect(url_for('notificaciones'))

@app.route('/rechazar_solicitud/<int:id_solicitante>', methods=['POST'])
@login_required
def rechazar_solicitud(id_solicitante):
    id_receptor = session['user_id']
    contact_request = Contact.query.filter_by(solicitante_id=id_solicitante, receptor_id=id_receptor, estado='pendiente').first()

    if contact_request:
        db.session.delete(contact_request)
        db.session.commit()
        flash(_('Solicitud de contacto rechazada.'), 'info')
    else:
        flash(_('No se pudo rechazar la solicitud.'), 'warning')
        
    return redirect(url_for('notificaciones'))


@app.route('/eliminar_contacto/<int:id_otro_usuario>', methods=['POST'])
@login_required
def eliminar_contacto(id_otro_usuario):
    user_id_actual = session['user_id']
    contact = Contact.query.filter(
        or_(
            and_(Contact.solicitante_id == user_id_actual, Contact.receptor_id == id_otro_usuario),
            and_(Contact.solicitante_id == id_otro_usuario, Contact.receptor_id == user_id_actual)
        )
    ).first()
    
    if contact:
        db.session.delete(contact)
        db.session.commit()
        flash(_('Contacto eliminado.'), 'success')
    else:
        flash(_('No se encontró una relación de contacto para eliminar.'), 'info')
        
    return redirect(request.referrer or url_for('contactos'))

@app.route('/enviar_solicitud/<int:id_receptor_solicitud>', methods=['POST'])
@login_required
def enviar_solicitud(id_solicitante_actual):
    id_receptor_solicitud = session['user_id']
    if not check_profile_completion(id_solicitante_actual):
        flash(_('Por favor, completa tu perfil antes de enviar solicitudes.'), 'warning')
        return redirect(request.referrer or url_for('profile'))

    if id_solicitante_actual == id_receptor_solicitud:
        flash(_('No puedes enviarte una solicitud a ti mismo.'), 'warning')
        return redirect(request.referrer or url_for('feed'))
    
    excluded_ids = get_blocked_and_blocking_ids(id_solicitante_actual)
    if id_receptor_solicitud in excluded_ids:
        flash(_('No puedes interactuar con este usuario.'), 'danger')
        return redirect(request.referrer or url_for('feed'))
        
    existing_contact = db.session.query(Contact).filter(
        or_(
            and_(Contact.solicitante_id == id_solicitante_actual, Contact.receptor_id == id_receptor_solicitud),
            and_(Contact.solicitante_id == id_receptor_solicitud, Contact.receptor_id == id_solicitante_actual)
        )
    ).first()
    
    if existing_contact:
        flash(_('Ya existe una solicitud o conexión con este usuario.'), 'info')
        return redirect(request.referrer or url_for('feed'))

    try:
        new_contact = Contact(solicitante_id=id_solicitante_actual, receptor_id=id_receptor_solicitud, estado='pendiente')
        db.session.add(new_contact)
        
        solicitante_perfil = db.session.query(Profile).filter_by(user_id=id_solicitante_actual).first()
        solicitante_slug = solicitante_perfil.slug if solicitante_perfil else "#"
        solicitante_nombre = solicitante_perfil.username if solicitante_perfil else _("Usuario")
        solicitante_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=solicitante_slug)}">@{solicitante_nombre}</a>'
        mensaje_notif = _('%(solicitante_link)s te ha enviado una solicitud de contacto.') % {'solicitante_link': solicitante_link_html}
        
        create_system_notification(id_receptor_solicitud, mensaje_notif, 'solicitud_contacto', id_solicitante_actual)
        
        db.session.commit()
        flash(_('Solicitud de contacto enviada.'), 'success')
    except Exception as e:
        db.session.rollback()
        flash(_('Error al enviar la solicitud.'), 'danger')
        print(f"Error en enviar_solicitud: {e}")
        
    return redirect(request.referrer or url_for('feed'))


@app.route('/aceptar_solicitud/<int:id_solicitante>', methods=['POST'])
@login_required
def aceptar_solicitud(id_solicitante):
    id_receptor_actual = session['user_id']
    
    excluded_ids = get_blocked_and_blocking_ids(id_receptor_actual)
    if id_solicitante in excluded_ids:
        flash(_('No puedes interactuar con este usuario.'), 'danger')
        return redirect(request.referrer or url_for('notificaciones'))

    contact_request = db.session.query(Contact).filter_by(
        solicitante_id=id_solicitante, 
        receptor_id=id_receptor_actual, 
        estado='pendiente'
    ).first()

    if contact_request:
        contact_request.estado = 'aceptado'
        
        receptor_perfil = db.session.query(Profile).filter_by(user_id=id_receptor_actual).first()
        receptor_slug = receptor_perfil.slug if receptor_perfil else "#"
        receptor_nombre = receptor_perfil.username if receptor_perfil else _("Usuario")
        receptor_link_html = f'<a href="{url_for("ver_perfil", slug_perfil=receptor_slug)}">@{receptor_nombre}</a>'
        mensaje_notif = _('%(receptor_link)s aceptó tu solicitud de contacto.') % {'receptor_link': receptor_link_html}
        
        create_system_notification(id_solicitante, mensaje_notif, 'solicitud_aceptada', id_receptor_actual)
        
        db.session.commit()
        flash(_('Solicitud de contacto aceptada.'), 'success')
    else:
        flash(_('No se pudo aceptar la solicitud (quizás ya no estaba pendiente o no era para ti).'), 'warning')
        
    return redirect(request.referrer or url_for('notificaciones'))


@app.route('/rechazar_solicitud/<int:id_solicitante>', methods=['POST'])
@login_required
def rechazar_solicitud(id_solicitante):
    id_receptor_actual = session['user_id']
    contact_request = db.session.query(Contact).filter_by(
        solicitante_id=id_solicitante, 
        receptor_id=id_receptor_actual, 
        estado='pendiente'
    ).first()

    if contact_request:
        db.session.delete(contact_request)
        db.session.commit()
        flash(_('Solicitud de contacto rechazada.'), 'info')
    else:
        flash(_('No se pudo rechazar la solicitud (quizás ya no estaba pendiente o no era para ti).'), 'warning')
        
    return redirect(request.referrer or url_for('notificaciones'))


@app.route('/eliminar_contacto/<int:id_otro_usuario>', methods=['POST'])
@login_required
def eliminar_contacto(id_otro_usuario):
    user_id_actual = session['user_id']
    contact_to_delete = db.session.query(Contact).filter(
        or_(
            and_(Contact.solicitante_id == user_id_actual, Contact.receptor_id == id_otro_usuario),
            and_(Contact.solicitante_id == id_otro_usuario, Contact.receptor_id == user_id_actual)
        )
    ).first()
    
    if contact_to_delete:
        db.session.delete(contact_to_delete)
        db.session.commit()
        flash(_('Contacto eliminado.'), 'success')
    else:
        flash(_('No se encontró una relación de contacto para eliminar.'), 'info')
        
    return redirect(request.referrer or url_for('feed'))


@app.route('/block_user/<int:user_to_block_id>', methods=['POST'])
@login_required
def block_user(user_to_block_id):
    blocker_id = session['user_id']

    if blocker_id == user_to_block_id:
        flash(_('No te puedes bloquear a ti mismo.'), 'warning')
        return redirect(request.referrer or url_for('feed'))

    try:
        existing_block = db.session.query(BlockedUser).filter_by(blocker_user_id=blocker_id, blocked_user_id=user_to_block_id).first()
        if not existing_block:
            new_block = BlockedUser(blocker_user_id=blocker_id, blocked_user_id=user_to_block_id)
            db.session.add(new_block)
        
        contact_to_delete = db.session.query(Contact).filter(
            or_(
                and_(Contact.solicitante_id == blocker_id, Contact.receptor_id == user_to_block_id),
                and_(Contact.solicitante_id == user_to_block_id, Contact.receptor_id == blocker_id)
            )
        ).first()
        if contact_to_delete:
            db.session.delete(contact_to_delete)
            
        db.session.commit()
        flash(_('Usuario bloqueado correctamente.'), 'success')
    except IntegrityError:
        db.session.rollback()
        flash(_('Este usuario ya está en tu lista de bloqueados.'), 'info')
    except Exception as e:
        db.session.rollback()
        flash(_('Ha ocurrido un error al intentar bloquear al usuario.'), 'danger')
        print(f"Error en block_user: {e}")

    return redirect(request.referrer or url_for('feed'))


@app.route('/unblock_user/<int:user_to_unblock_id>', methods=['POST'])
@login_required
def unblock_user(user_to_unblock_id):
    blocker_id = session['user_id']
    block_record = db.session.query(BlockedUser).filter_by(blocker_user_id=blocker_id, blocked_user_id=user_to_unblock_id).first()

    if block_record:
        db.session.delete(block_record)
        db.session.commit()
        flash(_('Usuario desbloqueado correctamente.'), 'success')
    else:
        flash(_('Este usuario no estaba en tu lista de bloqueados.'), 'info')

    return redirect(request.referrer or url_for('feed'))

@app.route('/ver_perfil/<slug_perfil>')
def ver_perfil(slug_perfil):
    if not slug_perfil or not slug_perfil.strip() or slug_perfil == "#":
        flash(_("No se puede acceder a un perfil sin un slug válido."), "danger")
        return redirect(url_for('feed'))

    profile = db.session.query(Profile).options(joinedload(Profile.user)).filter(Profile.slug.ilike(slug_perfil)).first()
    if not profile:
        flash(_("Perfil no encontrado."), "danger")
        return redirect(url_for('feed'))

    user_id_visitante = session.get('user_id')
    id_dueño_perfil = profile.user_id

    if user_id_visitante:
        excluded_ids = get_blocked_and_blocking_ids(user_id_visitante)
        if id_dueño_perfil in excluded_ids:
            flash(_('No puedes ver el perfil de este usuario.'), 'danger')
            return redirect(url_for('feed'))

    # Lógica para obtener el feed del perfil (publicaciones y compartidos)
    posts_q = db.session.query(
        Post.id.label("item_id"), 
        Post.timestamp.label("activity_timestamp"), 
        db.literal("original_post").label("item_type")
    ).filter(Post.user_id == id_dueño_perfil, Post.is_visible == True)

    shares_q = db.session.query(
        SharedPost.id.label("item_id"),
        SharedPost.timestamp.label("activity_timestamp"),
        db.literal("shared_post").label("item_type")
    ).join(Post, Post.id == SharedPost.original_post_id).filter(
        SharedPost.user_id == id_dueño_perfil,
        Post.is_visible == True
    )
    
    profile_feed_query = posts_q.union_all(shares_q).order_by(desc("activity_timestamp")).limit(50)
    
    profile_items = []
    for item in profile_feed_query.all():
        item_id, item_type = item.item_id, item.item_type
        if item_type == 'original_post':
            post = db.session.query(Post).options(joinedload(Post.author).joinedload(User.profile)).get(item_id)
            if post: profile_items.append({'item_type': 'original_post', 'data': post})
        elif item_type == 'shared_post':
            shared_post = db.session.query(SharedPost).options(joinedload(SharedPost.original_post)).get(item_id)
            if shared_post: profile_items.append({'item_type': 'shared_post', 'data': shared_post})

    # Lógica de estado de contacto
    estado_contacto, puede_enviar_solicitud, solicitud_pendiente_aqui = None, False, False
    if user_id_visitante and user_id_visitante != id_dueño_perfil:
        contacto = db.session.query(Contact).filter(
            or_( and_(Contact.solicitante_id == user_id_visitante, Contact.receptor_id == id_dueño_perfil),
                 and_(Contact.solicitante_id == id_dueño_perfil, Contact.receptor_id == user_id_visitante) )
        ).first()
        if contacto:
            estado_contacto = contacto.estado
            if estado_contacto == 'pendiente' and contacto.solicitante_id == id_dueño_perfil:
                solicitud_pendiente_aqui = True
        else:
            puede_enviar_solicitud = True
    
    visitante_ha_bloqueado = False
    if user_id_visitante:
        visitante_ha_bloqueado = db.session.query(BlockedUser).filter_by(blocker_user_id=user_id_visitante, blocked_user_id=id_dueño_perfil).first() is not None

    return render_template('ver_perfil.html',
                           profile_user_id=id_dueño_perfil,
                           username_perfil=profile.username,
                           bio=profile.bio, photo=profile.photo,
                           publicaciones=profile_items,
                           contacto_estado=estado_contacto,
                           puede_enviar_solicitud=puede_enviar_solicitud,
                           visitante_user_id=user_id_visitante,
                           slug_del_perfil=profile.slug,
                           es_propio_perfil=(user_id_visitante == id_dueño_perfil),
                           solicitud_pendiente_aqui=solicitud_pendiente_aqui,
                           visitante_ha_bloqueado=visitante_ha_bloqueado)


@app.cli.command("init-db")
def init_db_command():
    """Limpia los datos existentes y crea nuevas tablas."""
    with app.app_context():
        print("Eliminando todas las tablas existentes...")
        db.drop_all()
        print("Creando nuevas tablas basadas en los modelos...")
        db.create_all()
        
        print("Poblando secciones iniciales...")
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
            new_section = Section(**section_data)
            db.session.add(new_section)

        db.session.commit()
        print("Base de datos inicializada y secciones pobladas correctamente.")

@app.cli.command("regenerate-slugs")
def regenerate_slugs_command():
    """Regenera los slugs faltantes para los perfiles."""
    regenerar_slugs_si_faltan()
    
@app.route('/api/report/content', methods=['POST'])
@login_required_api
def report_content():
    """
    Gestiona la recepción de un nuevo reporte de contenido desde el frontend.
    """
    reporter_user_id = session['user_id']
    data = request.get_json()

    content_type = data.get('content_type')
    content_id = data.get('content_id')
    reason = data.get('reason')
    details = data.get('details', '').strip()

    if not all([content_type, content_id, reason]):
        return jsonify(success=False, error=_('Faltan datos en el reporte. Tipo, ID y motivo son obligatorios.')), 400

    if content_type not in ['post', 'comment', 'shared_post']:
        return jsonify(success=False, error=_('Tipo de contenido no válido.')), 400

    try:
        new_report = Report(
            reporter_user_id=reporter_user_id,
            content_type=content_type,
            content_id=content_id,
            reason=reason,
            details=details
        )
        db.session.add(new_report)
        db.session.commit()

        return jsonify(success=True, message=_('Reporte enviado correctamente. Gracias por ayudarnos a mantener la comunidad segura.'))

    except Exception as e:
        db.session.rollback()
        print(f"Error de base de datos al guardar el reporte: {e}")
        return jsonify(success=False, error=_('Ocurrió un error en el servidor al procesar tu reporte.')), 500
    
# --- RUTAS API (BLOQUE COMPLETO) ---

@app.route('/api/notificacion/marcar_leida/<int:notificacion_id>', methods=['POST'])
@login_required_api
def marcar_notificacion_leida(notificacion_id):
    notif = db.session.query(Notification).filter_by(id=notificacion_id, user_id=session['user_id']).first()
    if notif and not notif.leida:
        notif.leida = True
        db.session.commit()
        return jsonify(success=True)
    return jsonify(success=False), 404

@app.route('/api/mensajes/enviar', methods=['POST'])
@check_sanctions_and_block_api
def api_enviar_mensaje():
    user_id_actual = session['user_id']
    data = request.get_json()
    conversation_id = data.get('conversation_id')
    body = data.get('body', '').strip()

    if not all([conversation_id, body]):
        return jsonify(success=False, error=_("Faltan datos.")), 400

    participant = db.session.query(ConversationParticipant).filter_by(conversation_id=conversation_id, user_id=user_id_actual).first()
    if not participant:
        return jsonify(success=False, error=_("No tienes permiso para esta conversación.")), 403

    other_participant = db.session.query(ConversationParticipant).filter(ConversationParticipant.conversation_id == conversation_id, ConversationParticipant.user_id != user_id_actual).first()
    if other_participant:
        excluded_ids = get_blocked_and_blocking_ids(user_id_actual)
        if other_participant.user_id in excluded_ids:
            return jsonify(success=False, error=_("No puedes enviar mensajes a este usuario.")), 403
    
    try:
        timestamp_actual = datetime.now(timezone.utc)
        new_message = Message(conversation_id=conversation_id, sender_id=user_id_actual, body=body, timestamp=timestamp_actual)
        db.session.add(new_message)
        participant.conversation.updated_at = timestamp_actual
        db.session.commit()
        
        sender_profile = db.session.query(Profile).filter_by(user_id=user_id_actual).first()
        
        return jsonify(success=True, message={
            'id': new_message.id, 'sender_id': user_id_actual, 'body': body,
            'timestamp': timestamp_actual.strftime('%Y-%m-%d %H:%M:%S'),
            'username': sender_profile.username if sender_profile else _("Usuario"),
            'photo': sender_profile.photo if sender_profile else None
        })
    except Exception as e:
        db.session.rollback()
        print(f"Error al enviar mensaje: {e}")
        return jsonify(success=False, error=_("Error al enviar el mensaje.")), 500

@app.route('/api/users/mention_search')
@login_required_api
def mention_search():
    search_term = request.args.get('term', '').strip()
    if not search_term or len(search_term) < 1:
        return jsonify([])

    current_user_id = session['user_id']
    excluded_ids = get_blocked_and_blocking_ids(current_user_id)
    excluded_ids.add(current_user_id)

    query_filter = or_(Profile.username.ilike(f'%{search_term}%'), Profile.slug.ilike(f'%{search_term}%'))
    
    users_found = db.session.query(Profile).filter(query_filter, Profile.user_id.notin_(excluded_ids), Profile.username != None, Profile.slug != None).order_by(Profile.username.asc()).limit(10).all()
    
    suggestions = [{
        'username': p.username, 
        'slug': p.slug, 
        'photo': url_for('static', filename=f'uploads/{p.photo}') if p.photo else None
    } for p in users_found]
    
    return jsonify(suggestions)

@app.route('/stream-notifications')
def stream_notifications():
    if 'user_id' not in session:
        return Response(status=401)
    
    user_id = session['user_id']
    def event_stream():
        # Esta función puede permanecer como está, ya que su lógica de BBDD es simple y se reescribirá
        # al refactorizar el context_processor, que es la fuente de sus datos.
        # Por ahora, la dejamos para no introducir más cambios simultáneos.
        # En una refactorización final, usaríamos directamente las queries de SQLAlchemy aquí.
        # ... (código existente de event_stream) ...
        pass
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/privacy')
def privacy_policy():
    """Renderiza la página de la Política de Privacidad."""
    return render_template('privacy_policy.html')

@app.route('/terms')
def terms_of_service():
    """Renderiza la página de los Términos de Servicio."""
    return render_template('terms_of_service.html')

@app.route('/dev-login/<username>')
def dev_login(username):
    # --- IMPORTANTE: Esta ruta es solo para desarrollo local ---
    if app.debug: # Solo funciona si la app se inicia con debug=True
        user = User.query.filter_by(username=username).first()
        if user:
            session['user_id'] = user.id
            session['username_login'] = user.username
            flash(f'Has iniciado sesión como {username} en modo desarrollador.', 'success')
            return redirect(url_for('feed'))
    # Si no estamos en modo debug (como en Railway), esta ruta no hará nada.
    return "Acceso denegado. Esta ruta solo está disponible en modo de depuración.", 403

@app.route('/accept-policies', methods=['GET', 'POST'])
@login_required
def accept_policies():
    user = db.session.query(User).get(session['user_id'])
    # Si ya las aceptó, redirigir al feed.
    if user.accepted_policies:
        return redirect(url_for('feed'))

    if request.method == 'POST':
        # Verificar que ambos checkboxes fueron marcados
        if 'privacy' in request.form and 'terms' in request.form:
            user.accepted_policies = True
            db.session.commit()
            flash(_('¡Gracias por aceptar nuestras políticas! Ya puedes disfrutar de PiVerse.'), 'success')
            return redirect(url_for('feed'))
        else:
            flash(_('Debes aceptar ambas políticas para poder continuar.'), 'danger')

    return render_template('accept_policies.html')

# Ruta para servir el archivo de validación de dominio de Pi
@app.route('/validation-key.txt')
def serve_validation_key():
    return send_from_directory(app.static_folder, 'validation-key.txt')

# --- RUTAS DE ADMINISTRACIÓN Y MODERACIÓN ---

# Inserta este bloque al final del archivo, antes de if __name__ == '__main__':

# --- RUTAS DE ADMINISTRACIÓN Y MODERACIÓN ---

@app.route('/admin/users')
@coordinator_or_admin_required
def admin_users_list():
    users = User.query.options(joinedload(User.profile)).order_by(User.id.asc()).all()
    current_user = User.query.get(session['user_id'])
    return render_template('admin/users_list.html', users_list=users, current_user_role=current_user.role)

@app.route('/admin/user/<int:user_id>/set_role', methods=['POST'])
@coordinator_or_admin_required
def admin_set_user_role(user_id):
    if user_id == session.get('user_id'):
        flash(_('No puedes cambiar tu propio rol.'), 'danger')
        return redirect(url_for('admin_users_list'))

    actor = User.query.get(session['user_id'])
    target_user = User.query.get(user_id)
    if not target_user:
        flash(_("El usuario que intentas modificar no existe."), 'danger')
        return redirect(url_for('admin_users_list'))

    new_role = request.form.get('role')
    target_user_current_role = target_user.role

    allowed_new_roles = []
    if actor.role == 'admin':
        allowed_new_roles = ['user', 'moderator', 'coordinator', 'admin']
    elif actor.role == 'coordinator':
        allowed_new_roles = ['user', 'moderator']
        if target_user_current_role in ['admin', 'coordinator']:
            flash(_('No tienes permiso para modificar a este usuario.'), 'danger')
            return redirect(url_for('admin_users_list'))

    if new_role and new_role in allowed_new_roles:
        target_user.role = new_role
        log_details = f"Cambió el rol de '{target_user_current_role}' a '{new_role}'."
        log_admin_action(actor.id, 'ROLE_CHANGE', target_user_id=user_id, details=log_details)
        db.session.commit()
        flash(_('El rol del usuario ha sido actualizado.'), 'success')
    else:
        flash(_('Rol no válido o sin permiso para asignarlo.'), 'danger')

    return redirect(url_for('admin_users_list'))

@app.route('/admin/user/<int:user_id>/sanction', methods=['POST'])
@coordinator_or_admin_required
def admin_sanction_user(user_id):
    admin_id = session['user_id']
    data = request.get_json()
    duration = data.get('duration')
    reason = data.get('reason', '').strip()

    if not reason and duration != 'lift_sanctions':
        return jsonify(success=False, error=_("El motivo de la sanción es obligatorio.")), 400

    if user_id == admin_id:
        return jsonify(success=False, error=_("No te puedes sancionar a ti mismo.")), 403
    
    target_user = User.query.get(user_id)
    if not target_user:
        return jsonify(success=False, error=_("Usuario no encontrado.")), 404

    banned_until, muted_until = None, None
    log_action = "USER_SANCTION"
    flash_message = ""

    if duration == 'lift_sanctions':
        target_user.banned_until = None
        target_user.muted_until = None
        target_user.ban_reason = None
        notification_message = _("Se han levantado todas las sanciones de tu cuenta.")
        log_details = f"Levantó todas las sanciones del usuario ID {user_id}."
        flash_message = _("Sanciones levantadas correctamente.")
    elif duration == 'permanent_ban':
        target_user.banned_until = datetime(9999, 12, 31)
        target_user.muted_until = None
        target_user.ban_reason = reason
        notification_message = _('Tu cuenta ha sido suspendida de forma permanente. Motivo: "%(reason)s"', reason=reason)
        log_details = f"Suspendió permanentemente al usuario ID {user_id}. Motivo: {reason}"
        flash_message = _("Usuario suspendido permanentemente.")
    else:
        try:
            days = int(duration.split('_')[0])
            end_date = datetime.now(timezone.utc) + timedelta(days=days)
            fecha_fin_sancion = format_datetime(end_date, 'long', locale=get_babel_locale())
            
            if 'mute' in duration:
                target_user.muted_until = end_date
                notification_message = _('Tu cuenta ha sido silenciada hasta el %(date)s. Motivo: "%(reason)s"', date=fecha_fin_sancion, reason=reason)
                log_details = f"Silenció al usuario ID {user_id} hasta {fecha_fin_sancion}. Motivo: {reason}"
                flash_message = _("Usuario silenciado correctamente.")
            else: # Baneo temporal
                target_user.banned_until = end_date
                target_user.ban_reason = reason
                notification_message = _('Tu cuenta ha sido suspendida hasta el %(date)s. Motivo: "%(reason)s"', date=fecha_fin_sancion, reason=reason)
                log_details = f"Suspendió al usuario ID {user_id} hasta {fecha_fin_sancion}. Motivo: {reason}"
                flash_message = _("Usuario suspendido correctamente.")
        except (ValueError, IndexError):
            return jsonify(success=False, error=_("Duración de sanción no válida.")), 400

    create_system_notification(user_id, notification_message, 'sanction', user_id)
    log_admin_action(admin_id, log_action, target_user_id=user_id, details=log_details)
    db.session.commit()

    flash(flash_message, 'success')
    return jsonify(success=True)

@app.route('/admin/posts')
@moderator_or_higher_required
def admin_list_posts():
    posts = Post.query.order_by(Post.timestamp.desc()).all()
    # Para simplificar, no incluimos shared_posts en esta lista. Se pueden gestionar desde los reportes.
    return render_template('admin/posts_list.html', posts_list=posts)

@app.route('/admin/comments')
@moderator_or_higher_required
def admin_list_comments():
    comments = Comment.query.order_by(Comment.timestamp.desc()).all()
    return render_template('admin/comments_list.html', comments_list=comments)

@app.route('/admin/reports')
@moderator_or_higher_required
def admin_list_reports():
    pending_reports = Report.query.filter_by(status='pending').order_by(Report.created_at.asc()).all()
    reports_list = []
    for report in pending_reports:
        content = None
        content_url = "#"
        reported_user = None
        if report.content_type == 'post':
            content = Post.query.get(report.content_id)
            if content: content_url = url_for('ver_publicacion_individual', post_id_vista=content.id)
        elif report.content_type == 'comment':
            content = Comment.query.get(report.content_id)
            if content: content_url = url_for('ver_publicacion_individual', post_id_vista=content.post_id, _anchor=f"comment-{content.id}")
        
        if content:
            reported_user = content.author
        
        reports_list.append({
            'report': report,
            'content': content,
            'reported_user': reported_user,
            'content_url': content_url
        })
    
    return render_template('admin/reports_list.html', reports_list=reports_list)

@app.route('/admin/report/<int:report_id>/resolve', methods=['POST'])
@moderator_or_higher_required
def resolve_report(report_id):
    # ... Lógica adaptada para resolver reportes ...
    # (Esta función es compleja, pero su adaptación a SQLAlchemy implica reemplazar
    # las llamadas a c.execute por db.session.query, .add, .commit, etc.)
    return jsonify(success=True) # Respuesta simplificada

@app.route('/admin/appeals')
@coordinator_or_admin_required
def admin_list_appeals():
    pending_appeals = Appeal.query.filter_by(status='pending').order_by(Appeal.created_at.asc()).all()
    # ... lógica similar a admin_list_reports para obtener detalles ...
    return render_template('admin/appeals_list.html', appeals_list=pending_appeals)

@app.route('/admin/appeal/<int:appeal_id>/resolve', methods=['POST'])
@coordinator_or_admin_required
def resolve_appeal(appeal_id):
    # ... Lógica adaptada para resolver apelaciones ...
    return jsonify(success=True) # Respuesta simplificada

@app.route('/admin/log')
@coordinator_or_admin_required
def admin_view_log():
    logs = ActionLog.query.order_by(ActionLog.timestamp.desc()).limit(200).all()
    return render_template('admin/log_list.html', logs=logs)

# Inserta este bloque al final de tu app.py

# --- RUTAS DE ADMINISTRACIÓN Y MODERACIÓN ---

@app.route('/admin/users')
@coordinator_or_admin_required
def admin_users_list():
    users_list = User.query.options(joinedload(User.profile)).order_by(User.id.asc()).all()
    current_user = User.query.get(session['user_id'])
    return render_template('admin/users_list.html', users_list=users_list, current_user_role=current_user.role)

@app.route('/admin/user/<int:user_id>/set_role', methods=['POST'])
@coordinator_or_admin_required
def admin_set_user_role(user_id):
    if user_id == session.get('user_id'):
        flash(_('No puedes cambiar tu propio rol.'), 'danger')
        return redirect(url_for('admin_users_list'))

    actor = User.query.get(session['user_id'])
    target_user = User.query.get(user_id)
    if not target_user:
        flash(_("El usuario que intentas modificar no existe."), 'danger')
        return redirect(url_for('admin_users_list'))

    new_role = request.form.get('role')
    target_user_current_role = target_user.role

    allowed_to_assign = []
    if actor.role == 'admin':
        allowed_to_assign = ['user', 'moderator', 'coordinator', 'admin']
    elif actor.role == 'coordinator':
        allowed_to_assign = ['user', 'moderator']
        if target_user_current_role in ['admin', 'coordinator']:
            flash(_('No tienes permiso para modificar a este usuario.'), 'danger')
            return redirect(url_for('admin_users_list'))

    if new_role and new_role in allowed_to_assign:
        target_user.role = new_role
        log_details = f"Cambió el rol del usuario de '{target_user_current_role}' a '{new_role}'."
        log_admin_action(actor.id, 'ROLE_CHANGE', target_user_id=user_id, details=log_details)
        db.session.commit()
        flash(_('El rol del usuario ha sido actualizado.'), 'success')
    else:
        flash(_('Rol no válido o sin permiso para asignarlo.'), 'danger')

    return redirect(url_for('admin_users_list'))

@app.route('/admin/user/<int:user_id>/sanction', methods=['POST'])
@coordinator_or_admin_required
def admin_sanction_user(user_id):
    admin_id = session['user_id']
    data = request.get_json()
    duration = data.get('duration')
    reason = data.get('reason', '').strip()

    if not reason and duration != 'lift_sanctions':
        return jsonify(success=False, error=_("El motivo de la sanción es obligatorio.")), 400

    if user_id == admin_id:
        return jsonify(success=False, error=_("No te puedes sancionar a ti mismo.")), 403
    
    target_user = User.query.get(user_id)
    if not target_user:
        return jsonify(success=False, error=_("Usuario no encontrado.")), 404

    log_action = "USER_SANCTION"
    flash_message = ""
    notification_message = ""

    if duration == 'lift_sanctions':
        target_user.banned_until = None
        target_user.muted_until = None
        target_user.ban_reason = None
        notification_message = _("Se han levantado todas las sanciones de tu cuenta.")
        log_details = f"Levantó todas las sanciones del usuario ID {user_id}."
        flash_message = _("Sanciones levantadas correctamente.")
    elif duration == 'permanent_ban':
        target_user.banned_until = datetime(9999, 12, 31, tzinfo=timezone.utc)
        target_user.muted_until = None
        target_user.ban_reason = reason
        notification_message = _('Tu cuenta ha sido suspendida de forma permanente. Motivo: "%(reason)s"', reason=reason)
        log_details = f"Suspendió permanentemente al usuario ID {user_id}. Motivo: {reason}"
        flash_message = _("Usuario suspendido permanentemente.")
    else:
        try:
            days = int(duration.split('_')[0])
            end_date = datetime.now(timezone.utc) + timedelta(days=days)
            fecha_fin_sancion = format_datetime(end_date, 'long', locale=str(get_babel_locale()))
            
            if 'mute' in duration:
                target_user.muted_until = end_date
                notification_message = _('Tu cuenta ha sido silenciada hasta el %(date)s. Motivo: "%(reason)s"', date=fecha_fin_sancion, reason=reason)
                log_details = f"Silenció al usuario ID {user_id} hasta {end_date.strftime('%Y-%m-%d')}. Motivo: {reason}"
                flash_message = _("Usuario silenciado correctamente.")
            else:
                target_user.banned_until = end_date
                target_user.ban_reason = reason
                notification_message = _('Tu cuenta ha sido suspendida hasta el %(date)s. Motivo: "%(reason)s"', date=fecha_fin_sancion, reason=reason)
                log_details = f"Suspendió al usuario ID {user_id} hasta {end_date.strftime('%Y-%m-%d')}. Motivo: {reason}"
                flash_message = _("Usuario suspendido correctamente.")
        except (ValueError, IndexError):
            return jsonify(success=False, error=_("Duración de sanción no válida.")), 400

    create_system_notification(user_id, notification_message, 'sanction', user_id)
    log_admin_action(admin_id, log_action, target_user_id=user_id, details=log_details)
    db.session.commit()

    flash(flash_message, 'success')
    return jsonify(success=True)

@app.route('/admin/posts')
@moderator_or_higher_required
def admin_list_posts():
    # Usamos joinedload para cargar eficientemente los datos relacionados
    posts = Post.query.options(
        joinedload(Post.author).joinedload(User.profile),
        joinedload(Post.section)
    ).order_by(Post.timestamp.desc()).all()
    # Nota: Para simplificar, esta vista no mostrará los 'shared_posts'. Se pueden moderar a través de los reportes.
    # El HTML proporcionado tampoco los diferenciaba claramente, así que esta es la implementación más limpia.
    return render_template('admin/posts_list.html', posts_list=posts)

@app.route('/admin/comments')
@moderator_or_higher_required
def admin_list_comments():
    comments = Comment.query.options(
        joinedload(Comment.author).joinedload(User.profile),
        joinedload(Comment.post)
    ).order_by(Comment.timestamp.desc()).all()
    return render_template('admin/comments_list.html', comments_list=comments)

@app.route('/admin/post/<int:post_id>/edit', methods=['GET', 'POST'])
@moderator_or_higher_required
def admin_edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    original_content = post.content

    if request.method == 'POST':
        new_content = request.form.get('content', '').strip()
        if not new_content:
            flash(_('El contenido de la publicación no puede estar vacío.'), 'danger')
        else:
            post.content = new_content
            log_details = f"Editó el post ID {post_id}. Contenido anterior: '{original_content[:100]}...'"
            log_admin_action(session['user_id'], 'POST_EDIT_BY_MOD', target_content_id=post_id, details=log_details)
            db.session.commit()
            flash(_('Publicación actualizada correctamente.'), 'success')
            return redirect(url_for('admin_list_posts'))
    
    return render_template('admin/edit_post.html', post=post)

@app.route('/admin/reports')
@moderator_or_higher_required
def admin_list_reports():
    pending_reports = Report.query.filter_by(status='pending').order_by(Report.created_at.asc()).all()
    
    reports_list_for_template = []
    for report in pending_reports:
        content_obj = None
        content_url = "#"
        reported_user = None

        if report.content_type == 'post':
            content_obj = Post.query.get(report.content_id)
            if content_obj:
                content_url = url_for('ver_publicacion_individual', post_id_vista=content_obj.id)
                reported_user = content_obj.author
        elif report.content_type == 'comment':
            content_obj = Comment.query.get(report.content_id)
            if content_obj:
                content_url = url_for('ver_publicacion_individual', post_id_vista=content_obj.post_id, _anchor=f"comment-{content_obj.id}")
                reported_user = content_obj.author
        
        # Se añade esta lógica para que el template no falle
        item_for_template = {
            'id': report.id,
            'created_at': report.created_at,
            'reporter_username': report.reporter_user.profile.username if report.reporter_user and report.reporter_user.profile else 'N/A',
            'reported_user_username': reported_user.profile.username if reported_user and reported_user.profile else 'N/A',
            'reason': report.reason,
            'details': report.details,
            'content_url': content_url
        }
        reports_list_for_template.append(item_for_template)

    return render_template('admin/reports_list.html', 
                           reports_list=reports_list_for_template,
                           uphold_reasons=PREDEFINED_UPHOLD_REASONS,
                           dismiss_reasons=PREDEFINED_DISMISS_REASONS)

@app.route('/admin/appeals')
@coordinator_or_admin_required
def admin_list_appeals():
    pending_appeals = Appeal.query.filter_by(status='pending').order_by(Appeal.created_at.asc()).all()
    
    appeals_list_for_template = []
    for appeal in pending_appeals:
        # Lógica para construir la URL del contenido original
        content_url = "#"
        original_report = appeal.original_report
        if original_report.content_type == 'post':
            content_url = url_for('ver_publicacion_individual', post_id_vista=original_report.content_id)
        elif original_report.content_type == 'comment':
            comment = Comment.query.get(original_report.content_id)
            if comment:
                content_url = url_for('ver_publicacion_individual', post_id_vista=comment.post_id, _anchor=f"comment-{comment.id}")
        
        item_for_template = {
            'appeal_id': appeal.id,
            'appellant_username': appeal.appellant_user.profile.username,
            'appeal_text': appeal.appeal_text,
            'appeal_image_filename': appeal.appeal_image_filename,
            'original_report_id': appeal.original_report_id,
            'moderator_username': appeal.original_report.reviewed_by_user.profile.username if appeal.original_report.reviewed_by_user else 'N/A',
            'content_url': content_url
        }
        appeals_list_for_template.append(item_for_template)
        
    return render_template('admin/appeals_list.html', 
                           appeals_list=appeals_list_for_template,
                           approval_reasons=PREDEFINED_APPEAL_APPROVAL_REASONS,
                           denial_reasons=PREDEFINED_APPEAL_DENIAL_REASONS)

@app.route('/admin/log')
@coordinator_or_admin_required
def admin_view_log():
    logs = ActionLog.query.options(
        joinedload(ActionLog.actor_user).joinedload(User.profile),
        joinedload(ActionLog.target_user).joinedload(User.profile)
    ).order_by(ActionLog.timestamp.desc()).limit(200).all()
    return render_template('admin/log_list.html', logs=logs)



if __name__ == '__main__':
    app.run(debug=True)