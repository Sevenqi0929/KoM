from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return '部署成功！你的网站能访问了 ✅'

if __name__ == '__main__':
    app.run(host='0.0.0.0')
