from app.core import normalize_row, build_products

def test_normalize():
    a=normalize_row({'platform':'TikTok','id':'1','title':'Portable Blender','views':'10K','likes':'100'})
    assert a.platform=='TikTok' and a.views==10000

def test_products():
    rows=[
      normalize_row({'platform':'TikTok','id':'1','title':'Portable Blender','views':1000}),
      normalize_row({'platform':'Instagram','id':'2','title':'Portable Blender','impressions':2000}),
    ]
    p=build_products(rows)
    assert p and p[0]['platform_count']==2
