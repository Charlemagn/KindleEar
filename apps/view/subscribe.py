#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#管理订阅页面

import datetime, json, io, re, zipfile
from operator import attrgetter
from urllib.parse import urljoin
import xml.etree.ElementTree as ET
from flask import Blueprint, render_template, request, redirect, url_for, send_file
from flask_babel import gettext as _
from apps.base_handler import *
from apps.back_end.db_models import *
from apps.utils import str_to_bool
from lib.urlopener import UrlOpener
from books import BookClasses, BookClass
from config import *
from apps.view.library import KINDLEEAR_SITE, LIBRARY_MGR, SUBSCRIBED_FROM_LIBRARY, LIBRARY_GETSRC, buildKeUrl

bpSubscribe = Blueprint('bpSubscribe', __name__)

#管理我的订阅和杂志列表
@bpSubscribe.route("/my", endpoint='MySubscription')
@login_required()
def MySubscription(tips=None):
    user = get_login_user()
    titleToAdd = request.args.get('title_to_add')
    urlToAdd = request.args.get('url_to_add')
    myCustomRss = [item.to_dict(only=[Recipe.id, Recipe.title, Recipe.url, Recipe.isfulltext]) 
        for item in user.all_custom_rss()]
    myUploadedRecipes = [item.to_dict(only=[Recipe.id, Recipe.title, Recipe.description, Recipe.needs_subscription, Recipe.language]) 
        for item in user.all_uploaded_recipe()]
    #使用不同的id前缀区分不同的rss类型
    for item in myCustomRss:
        item['id'] = 'custom:{}'.format(item['id'])
    for item in myUploadedRecipes:
        item['id'] = 'upload:{}'.format(item['id'])
        item['language'] = item['language'].lower().replace('-', '_').split('_')[0]

    myBookedRecipes = json.dumps([item.to_dict(only=[BookedRecipe.recipe_id, BookedRecipe.separated, BookedRecipe.title,
        BookedRecipe.description, BookedRecipe.needs_subscription, BookedRecipe.account])
        for item in user.get_booked_recipe()], separators=(',', ':'))

    return render_template("my.html", tab="my", user=user, my_custom_rss=json.dumps(myCustomRss), tips=tips, 
        my_uploaded_recipes=json.dumps(myUploadedRecipes), my_booked_recipes=myBookedRecipes, 
        subscribe_url=url_for("bpSubscribe.MySubscription"), title_to_add=titleToAdd, url_to_add=urlToAdd)

#添加自定义RSS
@bpSubscribe.post("/my", endpoint='MySubscriptionPost')
@login_required()
def MySubscriptionPost():
    user = get_login_user()
    form = request.form
    title = form.get('rss_title')
    url = form.get('url')
    isfulltext = bool(form.get('fulltext'))
    if not title or not url:
        return redirect(url_for("bpSubscribe.MySubscription", tips=(_("Title or url is empty!"))))

    if not url.lower().startswith('http'): #http and https
        url = 'https://' + url

    #判断是否重复
    if url.lower() in [item.url.lower() for item in user.all_custom_rss()]:
        return redirect(url_for("bpSubscribe.MySubscription", tips=(_("Duplicated subscription!"))))

    Recipe(title=title, url=url, isfulltext=isfulltext, type_='custom', user=user.name,
        time=datetime.datetime.utcnow()).save()
    return redirect(url_for("bpSubscribe.MySubscription"))

#添加/删除自定义RSS订阅的AJAX处理函数
@bpSubscribe.post("/customrss/<actType>", endpoint='FeedsAjaxPost')
@login_required(forAjax=True)
def FeedsAjaxPost(actType):
    user = get_login_user()
    form = request.form
    actType = actType.lower()

    if actType == 'delete':
        rssId = form.get('id', '')
        recipeType, rssId = Recipe.type_and_id(rssId)
        rss = Recipe.get_by_id_or_none(rssId)
        if rss:
            rss.delete_instance()
            return {'status': 'ok'}
        else:
            return {'status': _('The Rss does not exist.')}
    elif actType == 'add':
        title = form.get('title', '')
        url = form.get('url', '')
        isfulltext = str_to_bool(form.get('fulltext', ''))
        fromSharedLibrary = str_to_bool(form.get('fromsharedlibrary', ''))
        recipeId = form.get('recipeId', '')

        respDict = {'status':'ok', 'title':title, 'url':url, 'isfulltext':isfulltext, 'recipeId': recipeId}

        if not title or not (url or recipeId):
            respDict['status'] = _("The Title or Url is empty.")
            return respDict

        #如果url不存在，则可能是分享的recipe，需要连接服务器获取recipe代码
        if not url:
            opener = UrlOpener()
            if recipeId.startswith('http'):
                resp = opener.open(recipeId)
            else:
                path = LIBRARY_MGR + LIBRARY_GETSRC
                resp = opener.open(buildKeUrl(path), {'recipeId': recipeId})

            if resp.status_code != 200:
                respDict['status'] = _("Failed to fetch the recipe.")
                return respDict

            if recipeId.startswith('http'):
                content = resp.text
            else:
                data = resp.json()
                if data.get('status') != 'ok':
                    respDict['status'] = data.get('status', '')
                    return respDict
                content = data.get('content', '')
                try:
                    params = SaveRecipeIfCorrect(user, content)
                except Exception as e:
                    return {'status': _("Failed to save the recipe. Error:") + str(e)}
                respDict.update(params)
        else: #自定义RSS
            if not url.lower().startswith('http'):
                url = 'https://' + url
                respDict['url'] = url

            #判断是否重复
            if url.lower() in [item.url.lower() for item in user.all_custom_rss()]:
                respDict['status'] = _("Duplicated subscription!")
                return respDict

            rss = Recipe(title=title, url=url, isfulltext=isfulltext, type_='custom', user=user.name,
                time=datetime.datetime.utcnow())
            rss.save()
            respDict['id'] = rss.recipe_id
        
        #如果是从共享库中订阅的，则通知共享服务器，提供订阅数量信息，以便排序
        if fromSharedLibrary:
            SendNewSubscription(title, url, recipeId)

        return respDict
    else:
        return {'status': 'Unknown command: {}'.format(actType)}

#获取保存有所有内置recipe的xml文件
@bpSubscribe.route("/builtin_recipes.xml", endpoint='BuiltInRecipesXml')
@login_required(forAjax=True)
def BuiltInRecipesXml():
    return send_file(os.path.join(appDir, 'books/builtin_recipes.xml'), mimetype="text/xml", as_attachment=False)

#通知共享服务器，有一个新的订阅
def SendNewSubscription(title, url, recipeId):
    opener = UrlOpener()
    path = LIBRARY_MGR + SUBSCRIBED_FROM_LIBRARY
    #只管杀不管埋，不用管能否成功了
    opener.open(buildKeUrl(path), {'title': title, 'url': url, 'recipeId': recipeId})

#订阅/退订内置或上传Recipe的AJAX处理函数
@bpSubscribe.post("/recipe/<actType>", endpoint='RecipeAjaxPost')
@login_required()
def RecipeAjaxPost(actType):
    user = get_login_user()
    form = request.form

    if actType == 'upload': #上传Recipe
        return SaveUploadedRecipe(user)
    
    recipeId = form.get('id', '')
    recipeType, dbId = Recipe.type_and_id(recipeId)
    if recipeType == 'builtin':
        recipe = GetBuiltinRecipe(recipeId)
    else:
        recipe = Recipe.get_by_id_or_none(dbId)

    if not recipe:
        return {'status': _('The recipe does not exist.')}

    if recipeType == 'builtin':
        title = recipe.get('title', '')
        desc = recipe.get('description', '')
        needSubscription = recipe.get('needs_subscription', False)
    else:
        title = recipe.title
        desc = recipe.description
        needSubscription = recipe.needs_subscription

    if actType == 'unsubscribe': #退订
        dbInst = user.get_booked_recipe(recipeId)
        if dbInst:
            dbInst.delete_instance()
        return {'status':'ok', 'id': recipeId, 'title': title, 'desc': desc}
    elif actType == 'subscribe': #订阅
        separated = str_to_bool(form.get('separated', ''))
        respDict = {'status': 'ok'}
        
        dbInst = user.get_booked_recipe(recipeId)
        if dbInst: #可以更新separated属性
            dbInst.separated = separated
            dbInst.save()
        else:
            BookedRecipe(recipe_id=recipeId, separated=separated, user=user.name, title=title, 
                description=desc, needs_subscription=needSubscription,
                time=datetime.datetime.utcnow()).save()

        respDict['title'] = title
        respDict['desc'] = desc
        respDict['needs_subscription'] = needSubscription
        respDict['separated'] = separated
        return respDict
    elif actType == 'delete': #删除已经上传的recipe
        if recipeType == 'builtin':
            return {'status': _('You can only delete the uploaded recipe')}

        dbInst = BookedRecipe.get_one(BookedRecipe.recipe_id == recipeId)
        if dbInst:
            dbInst.delete_instance()
        recipe.delete_instance()
        return {'status': 'ok', 'id': recipeId}
    else:
        return {'status': 'Unknown command: {}'.format(actType)}

#将上传的Recipe保存到数据库，返回一个结果字典，里面是一些recipe的元数据
def SaveUploadedRecipe(user):
    tips = ''
    try:
        data = request.files.get('recipe_file').read()
    except Exception as e:
        data = None
        tips = str(e)
        
    if not data:
        return {'status': _("Can not read uploaded file, Error:") + '\n' + tips}

    #尝试解码
    match = re.search(br'coding[:=]\s*([-\w.]+)', data[:200])
    enc = match.group(1).decode('utf-8') if match else 'utf-8'
    try:
        src = data.decode(enc)
    except:
        return {'status': _("Failed to decode the recipe. Please ensure that your recipe is saved in utf-8 encoding.")}

    try:
        params = SaveRecipeIfCorrect(user, src)
    except Exception as e:
        return {'status': _("Failed to save the recipe. Error:") + str(e)}

    params['status'] = 'ok'
    return params

#尝试编译recipe代码，如果成功并且数据库中不存在，则保存到数据库
#如果失败则抛出异常，否则返回一个元数据字典
def SaveRecipeIfCorrect(user: KeUser, src: str):
    from calibre.web.feeds.recipes import compile_recipe

    recipe = compile_recipe(src)
    
    #判断是否重复
    oldRecipe = Recipe.get_one(Recipe.title == recipe.title)
    if oldRecipe:
        raise Exception(_('The recipe is already in the library'))

    params = {"title": recipe.title, "description": recipe.description, "type_": 'upload', 
        "needs_subscription": recipe.needs_subscription, "content": src, "time": datetime.datetime.utcnow(),
        "user": user.name, "language": recipe.language}
    dbInst = Recipe(**params)
    dbInst.save()
    params.pop('content')
    params.pop('time')
    params.pop('type_')
    params['id'] = dbInst.recipe_id
    params['language'] = params['language'].lower().replace('-', '_').split('_')[0]
    return params

#修改Recipe的网站登陆信息
@bpSubscribe.post("/recipelogininfo", endpoint='RecipeLoginInfoPostAjax')
@login_required()
def RecipeLoginInfoPostAjax():
    user = get_login_user()
    id_ = request.form.get('id', '')
    account = request.form.get('account')
    password = request.form.get('password')
    recipe = BookedRecipe.get_one(BookedRecipe.recipe_id == id_)
    if not recipe:
        return {'status': _('The recipe does not exist.')}

    #任何一个留空则删除登陆信息
    ret = {'status': 'ok'}
    if not account or not password:
        recipe.account = ''
        recipe.password = ''
        ret['result'] = _('The login information for this recipe has been cleared')
    else:
        recipe.account = account
        recipe.password = password
        ret['result'] =  _('The login information for this recipe has been saved')
    recipe.save()
    return ret

#查看特定recipe的源码，将python源码转换为html返回
@bpSubscribe.route("/viewsrc/<id_>", endpoint='ViewRecipeSourceCode')
@login_required()
def ViewRecipeSourceCode(id_):
    from lib.python_highlighter import make_html
    htmlTpl = """<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>{title}</title></head><body>{body}</body></html>"""
    recipeId = id_.replace('__', ':')
    recipeType, dbId = Recipe.type_and_id(recipeId)
    if recipeType == 'upload':
        recipe = Recipe.get_by_id_or_none(dbId)
        if not recipe or not recipe.content:
            return htmlTpl.format(title="Error", body=_('The recipe does not exist.'))

        return make_html(io.StringIO(recipe.content), recipe.title)
    else: #内置recipe
        recipe = GetBuiltinRecipe(recipeId)
        src = GetBuiltinRecipeContent(recipeId)
        if not recipe or not src:
            return htmlTpl.format(title="Error", body=_('The recipe does not exist.'))

        return make_html(io.StringIO(src), recipe.get('title'))

#根据ID查询内置Recipe基本信息，返回一个字典
#{title:, author:, language:, needs_subscription:, description:, id:}
def GetBuiltinRecipe(id_: str):
    if not id_:
        return None

    try:
        tree = ET.parse(os.path.join(appDir, 'books/builtin_recipes.xml'))
        root = tree.getroot()
    except:
        return None

    id_ = id_ if id_.startswith('builtin:') else f'builtin:{id_}'
    for child in root:
        attrs = child.attrib
        if attrs.get('id', '') == id_:
            return attrs
    return None

#返回特定ID的内置数据源码字符串
def GetBuiltinRecipeContent(id_: str):
    if not id_:
        return None

    id_ = id_ if id_.startswith('builtin:') else f'builtin:{id_}'
    filename = '{}.recipe'.format(id_[8:])
    try:
        with zipfile.ZipFile(os.path.join(appDir, 'books', 'builtin_recipes.zip'), 'r') as zf:
            return zf.read(filename).decode('utf-8')
    except Exception as e:
        default_log.warning('Read {} failed: {}'.format(filename, str(e)))
        return None