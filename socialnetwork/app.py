from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_migrate import Migrate
from flask_wtf.csrf import generate_csrf
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
import os
import uuid
import random
import pytz  # 新增：时区转换

# --------------------------
# 全局初始化（修复文件上传配置）
# --------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
# app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:mypassword@uwvpsmknjesqrzhmnudc.supabase.co:5432/postgres'
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 300,
    'pool_pre_ping': True
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 新增：时区配置（北京时间）
app.config['TIMEZONE'] = 'Asia/Shanghai'
local_tz = pytz.timezone(app.config['TIMEZONE'])

# 文件上传核心配置（修复路径和权限）
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB限制
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'bmp'}

# 确保上传文件夹存在并赋予正确权限
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
if os.name != 'nt':  # 非Windows系统设置权限
    os.chmod(UPLOAD_FOLDER, 0o755)

# 初始化扩展
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
csrf = None  # 禁用CSRF拦截避免文件上传被拦

# --------------------------
# 工具函数（强化文件上传容错 + 新增时区转换）
# --------------------------
def allowed_file(filename):
    """检查文件格式是否合法"""
    if not filename or '.' not in filename:
        return False
    return filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def get_secure_file_path(filename, prefix):
    """生成安全的文件保存路径（修复路径拼接）"""
    try:
        secure_name = secure_filename(filename)
        unique_name = f"{prefix}_{uuid.uuid4()}_{secure_name}"
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        db_path = f"uploads/{unique_name}"  # 数据库存储相对路径
        return save_path, db_path
    except Exception as e:
        flash(f"文件路径生成失败: {str(e)}", "danger")
        return None, None

def utc_to_local(utc_dt):
    """将UTC时间转换为本地时间（北京时间）"""
    if utc_dt is None:
        return None
    # 处理原生datetime（无时区）
    if utc_dt.tzinfo is None:
        utc_dt = pytz.utc.localize(utc_dt)
    return utc_dt.astimezone(local_tz)

# --------------------------
# 数据库模型（彻底修复反向引用冲突 + 保留所有字段）
# --------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    avatar = db.Column(db.String(200), default='default_avatar.png')
    background_image = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Diary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image_path = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    last_editor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    is_collaborative = db.Column(db.Boolean, default=False)
    likes_count = db.Column(db.Integer, default=0)
    join_permission = db.Column(db.String(20), default='open')  # open/private

    author = db.relationship('User', foreign_keys=[author_id], backref='created_diaries')
    last_editor = db.relationship('User', foreign_keys=[last_editor_id])
    # 唯一关系定义：backref自动生成Comment.diary
    comments = db.relationship('Comment', backref='diary', lazy=True, cascade='all, delete-orphan')
    stickers = db.relationship('Sticker', backref='diary', lazy=True, cascade='all, delete-orphan')

    @property
    def comments_count(self):
        return len(self.comments) if self.comments else 0
    
    @property
    def created_at_local(self):
        """返回本地时间（北京时间）"""
        return utc_to_local(self.created_at)
    
    @property
    def updated_at_local(self):
        """返回本地更新时间"""
        return utc_to_local(self.updated_at)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    diary_id = db.Column(db.Integer, db.ForeignKey('diary.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    author = db.relationship('User', backref='comments')
    
    @property
    def created_at_local(self):
        return utc_to_local(self.created_at)

class Sticker(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(50), nullable=False, default='profile')
    content = db.Column(db.String(200), nullable=False)
    position_x = db.Column(db.Integer, default=0)
    position_y = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    diary_id = db.Column(db.Integer, db.ForeignKey('diary.id'), nullable=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    target_user = db.relationship('User', backref='stickers')
    
    @property
    def created_at_local(self):
        return utc_to_local(self.created_at)

class TimeCapsule(db.Model):
    id = db.Column(db.String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(100), nullable=False)
    content_user1 = db.Column(db.Text, nullable=False)
    content_user2 = db.Column(db.Text)
    image_path = db.Column(db.String(200))
    open_date = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_opened = db.Column(db.Boolean, default=False)

    user1 = db.relationship('User', foreign_keys=[user1_id], backref='created_capsules')
    user2 = db.relationship('User', foreign_keys=[user2_id], backref='received_capsules')
    
    @property
    def created_at_local(self):
        return utc_to_local(self.created_at)
    
    @property
    def open_date_local(self):
        return utc_to_local(self.open_date)

class DailyCheckin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question = db.Column(db.String(200), nullable=False)
    answer = db.Column(db.Boolean)
    checkin_date = db.Column(db.Date, default=date.today(), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='checkins')
    
    @property
    def created_at_local(self):
        return utc_to_local(self.created_at)

class Nest(db.Model):
    __tablename__ = 'nest'
    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    background_image = db.Column(db.String(200))

    user1 = db.relationship('User', foreign_keys=[user1_id], backref=db.backref('nest_as_user1', lazy=True))
    user2 = db.relationship('User', foreign_keys=[user2_id], backref=db.backref('nest_as_user2', lazy=True))
    
    @property
    def created_at_local(self):
        return utc_to_local(self.created_at)

class NestDiary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nest_id = db.Column(db.Integer, db.ForeignKey('nest.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    user1_diary_id = db.Column(db.Integer, db.ForeignKey('diary.id'))
    user2_diary_id = db.Column(db.Integer, db.ForeignKey('diary.id'))
    common_points = db.Column(db.String(200))

    user1_diary = db.relationship('Diary', foreign_keys=[user1_diary_id])
    user2_diary = db.relationship('Diary', foreign_keys=[user2_diary_id])
    nest = db.relationship('Nest', backref='diaries')

# --------------------------
# 全局配置（保留原有 + 新增时区过滤器）
# --------------------------
CHECKIN_QUESTIONS = [
    "Did you have breakfast today?",
    "Did you dream last night?",
    "Did you drink enough water today?",
    "Did you miss me today?",
    "Did you smile at something sweet today?",
    "Did you get enough sleep last night?",
    "Did you eat something delicious today?",
    "Did you take a walk outside today?",
    "Did you listen to our favorite song today?",
    "Did you write down a small wish today?",
    "Did you feel warm today?",
    "Did you hug someone (or a pillow) today?",
    "Did you take a nice photo today?",
    "Did you finish what you planned today?",
    "Did you think of a joke to tell me today?"
]

# 新增：模板时区过滤器
@app.template_filter('local_time')
def local_time_filter(dt, format='%Y-%m-%d %H:%M:%S'):
    """模板中转换UTC时间为本地时间"""
    local_dt = utc_to_local(dt)
    return local_dt.strftime(format) if local_dt else ""

@app.context_processor
def inject_common_data():
    return dict(
        datetime=datetime,
        csrf_token=generate_csrf,
        local_tz=local_tz,
        utc_to_local=utc_to_local
    )

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --------------------------
# 基础路由（保留原有功能 + 权限控制强化）
# --------------------------
@app.route('/')
def index():
    return redirect(url_for('home'))

# 修复后的 home 路由（无只读属性赋值）
@app.route('/home')
@login_required
def home():
    diaries = Diary.query.order_by(Diary.created_at.desc()).all()
    for diary in diaries:
        if diary.likes_count is None:
            diary.likes_count = 0
    return render_template('home.html', diaries=diaries)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and user.check_password(request.form['password']):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('home'))
        flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not username or not email or not password:
            flash('Username, email and password are required!', 'error')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered!', 'error')
            return redirect(url_for('register'))
        
        if User.query.filter_by(username=username).first():
            flash('Username already taken!', 'error')
            return redirect(url_for('register'))
        
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --------------------------
# 日记路由（修复图片上传 + 强化权限控制）
# --------------------------
@app.route('/create-diary', methods=['GET', 'POST'])
@login_required
def create_diary():
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        is_collab = request.form.get('is_collaborative') == 'on'
        
        new_diary = Diary(
            title=title,
            content=content,
            author_id=current_user.id,
            is_collaborative=is_collab,
            likes_count=0,
            join_permission=request.form.get('join_permission', 'open')
        )
        
        # 修复图片上传逻辑
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                save_path, db_path = get_secure_file_path(file.filename, 'diary')
                if save_path and db_path:
                    try:
                        file.save(save_path)
                        if os.name != 'nt':
                            os.chmod(save_path, 0o644)
                        new_diary.image_path = db_path
                        flash("图片上传成功！", "success")
                    except Exception as e:
                        flash(f"图片保存失败: {str(e)}", "danger")
                        print(f"日记图片上传错误: {e}")
        
        db.session.add(new_diary)
        db.session.commit()
        flash('Diary created successfully!', 'success')
        return redirect(url_for('home'))
    
    return render_template('create_diary.html')

@app.route('/edit-diary/<int:diary_id>', methods=['GET', 'POST'])
@login_required
def edit_diary(diary_id):
    diary = Diary.query.get_or_404(diary_id)
    
    # 强化权限控制：仅作者可编辑（无论是否协作）
    if diary.author_id != current_user.id:
        flash('You do not have permission to edit this diary! Only the author can edit.', 'danger')
        return redirect(url_for('view_diary', diary_id=diary_id))
    
    if request.method == 'POST':
        diary.title = request.form['title']
        diary.content = request.form['content']
        diary.last_editor_id = current_user.id
        diary.updated_at = datetime.utcnow()
        # 仅作者可修改权限
        diary.join_permission = request.form.get('join_permission', diary.join_permission)
        
        # 修复图片上传逻辑
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                save_path, db_path = get_secure_file_path(file.filename, 'diary')
                if save_path and db_path:
                    try:
                        file.save(save_path)
                        if os.name != 'nt':
                            os.chmod(save_path, 0o644)
                        diary.image_path = db_path
                    except Exception as e:
                        flash(f"图片保存失败: {str(e)}", "danger")
                        print(f"编辑日记图片上传错误: {e}")
        
        db.session.commit()
        flash('Diary updated successfully!', 'success')
        return redirect(url_for('view_diary', diary_id=diary.id))
    
    return render_template('edit_diary.html', diary=diary)

@app.route('/delete-diary/<int:diary_id>')
@login_required
def delete_diary(diary_id):
    diary = Diary.query.get_or_404(diary_id)
    
    if diary.author_id != current_user.id:
        flash('You do not have permission to delete this diary!', 'danger')
        return redirect(url_for('home'))
    
    # 删除关联评论和贴纸
    Comment.query.filter_by(diary_id=diary_id).delete()
    Sticker.query.filter_by(diary_id=diary_id).delete()
    
    # 删除日记图片文件
    if diary.image_path:
        try:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], diary.image_path.split('/')[-1])
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass
    
    db.session.delete(diary)
    db.session.commit()
    flash('Diary deleted successfully!', 'success')
    return redirect(url_for('home'))

@app.route('/view-diary/<int:diary_id>')
@login_required
def view_diary(diary_id):
    diary = Diary.query.get_or_404(diary_id)
    comments = Comment.query.filter_by(diary_id=diary_id).order_by(Comment.created_at.desc()).all()
    stickers = Sticker.query.filter_by(diary_id=diary_id).all()
    
    # 只需要初始化点赞数，不需要对 comments_count 赋值
    if diary.likes_count is None:
        diary.likes_count = 0
    
    # 权限判断：是否为作者
    is_author = diary.author_id == current_user.id
    # 权限判断：是否可申请协作（Open状态 + 非作者）
    can_request_collab = diary.join_permission == 'open' and not is_author
    
    return render_template('view_diary.html', 
                           diary=diary, 
                           comments=comments, 
                           stickers=stickers,
                           is_author=is_author,
                           can_request_collab=can_request_collab)

@app.route('/like-diary/<int:diary_id>', methods=['POST'])
@login_required
def like_diary(diary_id):
    diary = Diary.query.get_or_404(diary_id)
    if diary.likes_count is None:
        diary.likes_count = 0
    diary.likes_count += 1
    db.session.commit()
    return jsonify({'status': 'success', 'new_count': diary.likes_count})

# 新增：协作申请路由
@app.route('/request-collaborate/<int:diary_id>', methods=['POST'])
@login_required
def request_collaborate(diary_id):
    diary = Diary.query.get_or_404(diary_id)
    
    # 检查权限
    if diary.author_id == current_user.id:
        flash('You are the author of this diary!', 'warning')
        return redirect(url_for('view_diary', diary_id=diary_id))
    
    if diary.join_permission != 'open':
        flash('This diary is private - you cannot request to collaborate!', 'danger')
        return redirect(url_for('view_diary', diary_id=diary_id))
    
    flash('Collaboration request sent to the author!', 'success')
    return redirect(url_for('view_diary', diary_id=diary_id))

# --------------------------
# 评论路由（保留原有）
# --------------------------
@app.route('/add-comment/<int:diary_id>', methods=['POST'])
@login_required
def add_comment(diary_id):
    content = request.form.get('content', '').strip()
    if not content:
        flash('Comment content cannot be empty!', 'danger')
        return redirect(url_for('view_diary', diary_id=diary_id))
    
    new_comment = Comment(
        content=content,
        diary_id=diary_id,
        author_id=current_user.id
    )
    db.session.add(new_comment)
    db.session.commit()
    flash('Comment added!', 'success')
    return redirect(url_for('view_diary', diary_id=diary_id))

@app.route('/delete-comment/<int:comment_id>')
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    
    if comment.author_id != current_user.id and comment.diary.author_id != current_user.id:
        flash('You do not have permission to delete this comment!', 'danger')
        return redirect(url_for('home'))
    
    diary_id = comment.diary_id
    db.session.delete(comment)
    db.session.commit()
    flash('Comment deleted successfully!', 'success')
    return redirect(url_for('view_diary', diary_id=diary_id))

# --------------------------
# 个人主页路由（修复所有文件上传）
# --------------------------
@app.route('/profile/<int:user_id>')
@login_required
def profile(user_id):
    user = User.query.get_or_404(user_id)
    stickers = Sticker.query.filter_by(target_user_id=user.id).all()
    user_diaries = Diary.query.filter_by(author_id=user.id).order_by(Diary.created_at.desc()).all()
    
    today = date.today()
    random.seed(today.toordinal())
    today_question = random.choice(CHECKIN_QUESTIONS)
    today_checkin = DailyCheckin.query.filter_by(user_id=user.id, checkin_date=today).first()
    past_checkins = DailyCheckin.query.filter(
        DailyCheckin.user_id == user.id,
        DailyCheckin.checkin_date < today
    ).order_by(DailyCheckin.checkin_date.desc()).limit(7).all()
    
    nest_info = None
    nest = Nest.query.filter(
        (Nest.user1_id == user.id) | (Nest.user2_id == user.id)
    ).first()
    
    if nest:
        days_together = (datetime.utcnow().date() - nest.created_at.date()).days
        partner = nest.user2 if nest.user1_id == user.id else nest.user1
        nest_info = {
            'id': nest.id,
            'days_together': days_together,
            'user1_avatar': nest.user1.avatar or 'default_avatar.png',
            'user2_avatar': nest.user2.avatar or 'default_avatar.png',
            'user1_username': nest.user1.username,
            'user2_username': nest.user2.username,
            'background_image': None,
            'partner_username': partner.username,
            'partner_id': partner.id
        }
    
    available_users = User.query.filter(User.id != current_user.id).all()
    if nest:
        available_users = User.query.filter(
            User.id != current_user.id,
            User.id != nest.user1_id,
            User.id != nest.user2_id
        ).all()
    
    return render_template('profile.html', 
                           user=user,
                           user_diaries=user_diaries,
                           stickers=stickers,
                           today_question=today_question,
                           today_checkin=today_checkin,
                           past_checkins=past_checkins,
                           nest_info=nest_info,
                           available_users=available_users)

@app.route('/upload-avatar', methods=['POST'])
@login_required
def upload_avatar():
    """修复头像上传"""
    if 'avatar' not in request.files:
        flash('No avatar file selected!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    file = request.files['avatar']
    if file.filename == '':
        flash('No avatar file selected!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    if file and allowed_file(file.filename):
        save_path, db_path = get_secure_file_path(file.filename, 'avatar')
        if save_path and db_path:
            try:
                file.save(save_path)
                if os.name != 'nt':
                    os.chmod(save_path, 0o644)
                # 删除旧头像文件
                if current_user.avatar and current_user.avatar != 'default_avatar.png':
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.avatar.split('/')[-1])
                    if os.path.exists(old_path):
                        os.remove(old_path)
                current_user.avatar = db_path
                db.session.commit()
                flash('Avatar updated successfully!', 'success')
            except Exception as e:
                flash(f'Avatar upload failed: {str(e)}', 'danger')
                print(f"头像上传错误: {e}")
    else:
        flash('Invalid file type! Only PNG/JPG/GIF/BMP allowed.', 'danger')
    
    return redirect(url_for('profile', user_id=current_user.id))

@app.route('/upload-background', methods=['POST'])
@login_required
def upload_background():
    """修复背景图片上传"""
    if 'background' not in request.files:
        flash('No background file selected!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    file = request.files['background']
    if file.filename == '':
        flash('No background file selected!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    if file and allowed_file(file.filename):
        save_path, db_path = get_secure_file_path(file.filename, 'bg')
        if save_path and db_path:
            try:
                file.save(save_path)
                if os.name != 'nt':
                    os.chmod(save_path, 0o644)
                # 删除旧背景文件
                if current_user.background_image:
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.background_image.split('/')[-1])
                    if os.path.exists(old_path):
                        os.remove(old_path)
                current_user.background_image = db_path
                db.session.commit()
                flash('Background updated successfully!', 'success')
            except Exception as e:
                flash(f'Background upload failed: {str(e)}', 'danger')
                print(f"背景上传错误: {e}")
    else:
        flash('Invalid file type! Only PNG/JPG/GIF/BMP allowed.', 'danger')
    
    return redirect(url_for('profile', user_id=current_user.id))

@app.route('/add-sticker/<int:user_id>', methods=['POST'])
@login_required
def add_sticker(user_id):
    """修复贴纸上传"""
    if 'sticker' not in request.files:
        flash('No sticker file selected!', 'danger')
        return redirect(url_for('profile', user_id=user_id))
    
    file = request.files['sticker']
    if file.filename == '':
        flash('No sticker file selected!', 'danger')
        return redirect(url_for('profile', user_id=user_id))
    
    if file and allowed_file(file.filename):
        save_path, db_path = get_secure_file_path(file.filename, 'sticker')
        if save_path and db_path:
            try:
                file.save(save_path)
                if os.name != 'nt':
                    os.chmod(save_path, 0o644)
                new_sticker = Sticker(
                    type='profile',
                    content=db_path,
                    target_user_id=user_id,
                    position_x=int(request.form.get('x', 0)),
                    position_y=int(request.form.get('y', 0))
                )
                db.session.add(new_sticker)
                db.session.commit()
                flash('Sticker added successfully!', 'success')
            except Exception as e:
                flash(f'Sticker upload failed: {str(e)}', 'danger')
                print(f"贴纸上传错误: {e}")
    else:
        flash('Invalid file type! Only PNG/JPG/GIF/BMP allowed.', 'danger')
    
    return redirect(url_for('profile', user_id=user_id))

@app.route('/update-sticker/<int:sticker_id>', methods=['POST'])
@login_required
def update_sticker(sticker_id):
    sticker = Sticker.query.get_or_404(sticker_id)
    if sticker.target_user_id != current_user.id:
        return jsonify({'status': 'error'}), 403
    
    sticker.position_x = int(request.form.get('x', 0))
    sticker.position_y = int(request.form.get('y', 0))
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/delete-sticker/<int:sticker_id>')
@login_required
def delete_sticker(sticker_id):
    sticker = Sticker.query.get_or_404(sticker_id)
    if sticker.target_user_id != current_user.id:
        flash('Permission denied!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    # 删除贴纸文件
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], sticker.content.split('/')[-1])
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        print(f"删除贴纸文件错误: {e}")
    
    db.session.delete(sticker)
    db.session.commit()
    flash('Sticker deleted!', 'success')
    return redirect(url_for('profile', user_id=current_user.id))

@app.route('/submit-checkin', methods=['POST'])
@login_required
def submit_checkin():
    question = request.form['question']
    answer = request.form['answer'] == 'True'
    today = date.today()

    existing = DailyCheckin.query.filter_by(user_id=current_user.id, checkin_date=today).first()
    if existing:
        existing.question = question
        existing.answer = answer
    else:
        new_checkin = DailyCheckin(
            user_id=current_user.id,
            question=question,
            answer=answer,
            checkin_date=today
        )
        db.session.add(new_checkin)
    
    db.session.commit()
    flash('Check-in updated successfully!', 'success')
    return redirect(url_for('profile', user_id=current_user.id))

# --------------------------
# 时光胶囊路由（修复图片上传）
# --------------------------
@app.route('/create-time-capsule', methods=['GET', 'POST'])
@login_required
def create_time_capsule():
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        user2_id = request.form['partner_id']
        open_date = datetime.strptime(request.form['open_date'], '%Y-%m-%d')
        
        image_path = None
        # 修复图片上传
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                save_path, db_path = get_secure_file_path(file.filename, 'capsule')
                if save_path and db_path:
                    try:
                        file.save(save_path)
                        if os.name != 'nt':
                            os.chmod(save_path, 0o644)
                        image_path = db_path
                    except Exception as e:
                        flash(f"图片上传失败: {str(e)}", "danger")
                        print(f"时光胶囊图片上传错误: {e}")
        
        new_capsule = TimeCapsule(
            title=title,
            content_user1=content,
            user1_id=current_user.id,
            user2_id=user2_id,
            open_date=open_date,
            image_path=image_path
        )
        
        db.session.add(new_capsule)
        db.session.commit()
        flash('Time Capsule created successfully!', 'success')
        return redirect(url_for('time_capsule_list'))
    
    users = User.query.filter(User.id != current_user.id).all()
    today = date.today().strftime('%Y-%m-%d')
    return render_template('create_time_capsule.html', users=users, today=today)

@app.route('/time-capsule/<capsule_id>/add-content', methods=['GET', 'POST'])
@login_required
def add_capsule_content(capsule_id):
    capsule = TimeCapsule.query.get_or_404(capsule_id)
    
    if capsule.user2_id != current_user.id:
        flash('Permission denied!', 'danger')
        return redirect(url_for('time_capsule_list'))
    
    if request.method == 'POST':
        capsule.content_user2 = request.form['content']
        db.session.commit()
        flash('Content added to Time Capsule!', 'success')
        return redirect(url_for('time_capsule_detail', capsule_id=capsule.id))
    
    return render_template('add_capsule_content.html', capsule=capsule)

@app.route('/time-capsules')
@login_required
def time_capsule_list():
    capsules = TimeCapsule.query.filter(
        (TimeCapsule.user1_id == current_user.id) | (TimeCapsule.user2_id == current_user.id)
    ).order_by(TimeCapsule.created_at.desc()).all()
    return render_template('time_capsule_list.html', capsules=capsules)

@app.route('/time-capsule/<capsule_id>')
@login_required
def time_capsule_detail(capsule_id):
    capsule = TimeCapsule.query.get_or_404(capsule_id)
    
    if capsule.user1_id != current_user.id and capsule.user2_id != current_user.id:
        flash('Permission denied!', 'danger')
        return redirect(url_for('time_capsule_list'))
    
    can_open = datetime.utcnow() >= capsule.open_date
    if can_open and not capsule.is_opened:
        capsule.is_opened = True
        db.session.commit()
    
    return render_template('time_capsule_detail.html', capsule=capsule, can_open=can_open)

# --------------------------
# 小窝路由（保留原有）
# --------------------------
def sync_nest_diaries(nest_id):
    nest = Nest.query.get(nest_id)
    today = datetime.utcnow().date()
    
    user1_diary = Diary.query.filter(
        Diary.author_id == nest.user1_id,
        Diary.created_at >= datetime(today.year, today.month, today.day),
        Diary.created_at < datetime(today.year, today.month, today.day) + timedelta(days=1)
    ).first()
    
    user2_diary = Diary.query.filter(
        Diary.author_id == nest.user2_id,
        Diary.created_at >= datetime(today.year, today.month, today.day),
        Diary.created_at < datetime(today.year, today.month, today.day) + timedelta(days=1)
    ).first()
    
    common_points = ""
    if user1_diary and user2_diary:
        keywords = ['happy', 'sad', 'love', 'food', 'work', 'family', 'friend']
        user1_words = user1_diary.content.lower().split()
        user2_words = user2_diary.content.lower().split()
        common_keywords = [k for k in keywords if k in user1_words and k in user2_words]
        if common_keywords:
            common_points = f"Both mentioned: {', '.join(common_keywords)}"
        else:
            common_points = "Shared moments"
    
    existing = NestDiary.query.filter_by(nest_id=nest_id, date=today).first()
    if not existing:
        nest_diary = NestDiary(
            nest_id=nest_id,
            date=today,
            user1_diary_id=user1_diary.id if user1_diary else None,
            user2_diary_id=user2_diary.id if user2_diary else None,
            common_points=common_points
        )
        db.session.add(nest_diary)
        db.session.commit()

@app.route('/create-nest', methods=['POST'])
@login_required
def create_nest():
    partner_username = request.form.get('partner_username')
    partner = User.query.filter_by(username=partner_username).first()
    
    if not partner:
        flash('User not found!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    if partner.id == current_user.id:
        flash('You cannot invite yourself!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    old_nest = Nest.query.filter(
        (Nest.user1_id == current_user.id) | (Nest.user2_id == current_user.id)
    ).first()
    if old_nest:
        NestDiary.query.filter_by(nest_id=old_nest.id).delete()
        db.session.delete(old_nest)
        db.session.commit()
    
    new_nest = Nest(
        user1_id=current_user.id,
        user2_id=partner.id
    )
    db.session.add(new_nest)
    db.session.commit()
    
    sync_nest_diaries(new_nest.id)
    flash(f'Successfully created nest with {partner.username}!', 'success')
    return redirect(url_for('nest_page', nest_id=new_nest.id))

@app.route('/leave-nest/<int:nest_id>')
@login_required
def leave_nest(nest_id):
    nest = Nest.query.get_or_404(nest_id)
    
    if current_user.id not in [nest.user1_id, nest.user2_id]:
        flash('Permission denied!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    NestDiary.query.filter_by(nest_id=nest_id).delete()
    db.session.delete(nest)
    db.session.commit()
    flash('You have left the nest!', 'success')
    return redirect(url_for('profile', user_id=current_user.id))

@app.route('/nest/<int:nest_id>')
@login_required
def nest_page(nest_id):
    nest = Nest.query.get_or_404(nest_id)
    
    if current_user.id not in [nest.user1_id, nest.user2_id]:
        flash('Permission denied!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    days_together = (datetime.utcnow().date() - nest.created_at.date()).days
    nest_diaries = NestDiary.query.filter_by(nest_id=nest_id).order_by(NestDiary.date.desc()).all()
    processed_diaries = []
    for nd in nest_diaries:
        user1_diary = nd.user1_diary.content if nd.user1_diary else "No diary"
        user2_diary = nd.user2_diary.content if nd.user2_diary else "No diary"
        processed_diaries.append({
            'date': nd.date,
            'user1_diary': user1_diary,
            'user2_diary': user2_diary,
            'common_points': nd.common_points or "Shared moments"
        })
    
    nest_info = {
        'id': nest.id,
        'days_together': days_together,
        'user1_avatar': nest.user1.avatar or 'default_avatar.png',
        'user2_avatar': nest.user2.avatar or 'default_avatar.png',
        'user1_username': nest.user1.username,
        'user2_username': nest.user2.username,
        'background_image': None
    }
    
    return render_template('nest.html', 
                           nest=nest, 
                           nest_info=nest_info, 
                           nest_diaries=processed_diaries)

@app.route('/nest/<int:nest_id>/upload-background', methods=['POST'])
@login_required
def upload_nest_background(nest_id):
    return "This feature has been removed", 404

@app.route('/nest/<int:nest_id>/diary/<date>')
@login_required
def nest_diary_detail(nest_id, date):
    nest = Nest.query.get_or_404(nest_id)
    
    if current_user.id not in [nest.user1_id, nest.user2_id]:
        flash('Permission denied!', 'danger')
        return redirect(url_for('profile', user_id=current_user.id))
    
    diary_date = datetime.strptime(date, '%Y-%m-%d').date()
    nest_diary = NestDiary.query.filter_by(nest_id=nest_id, date=diary_date).first_or_404()
    
    user1_diary_content = nest_diary.user1_diary.content if nest_diary.user1_diary else "No diary"
    user2_diary_content = nest_diary.user2_diary.content if nest_diary.user2_diary else "No diary"
    
    keywords = ['happy', 'sad', 'love', 'food', 'work', 'family', 'friend']
    user1_words = user1_diary_content.lower().split()
    user2_words = user2_diary_content.lower().split()
    common_keywords = [k for k in keywords if k in user1_words and k in user2_words]
    
    auto_summary = f"Both mentioned: {', '.join(common_keywords)}" if common_keywords else "Shared moments"
    
    return render_template('nest_diary_detail.html', 
                           nest=nest,
                           nest_diary=nest_diary,
                           user1_diary_content=user1_diary_content,
                           user2_diary_content=user2_diary_content,
                           auto_summary=auto_summary)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=8854)
