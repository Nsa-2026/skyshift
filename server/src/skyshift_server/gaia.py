"""One Gaia field query reused by the API and overlays; Gaia-native units."""
import requests

def cone(ra,dec,r,*,cache=None):
    query=f"SELECT source_id,ra,dec,pmra,pmdec,parallax,phot_g_mean_mag FROM gaiadr3.gaia_source WHERE 1=CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{ra},{dec},{r/60})) AND phot_g_mean_mag<19"
    response=requests.post('https://gea.esac.esa.int/tap-server/tap/sync',data=dict(REQUEST='doQuery',LANG='ADQL',FORMAT='json',QUERY=query),timeout=60)
    response.raise_for_status();data=response.json();cols=[c['name'] for c in data['metadata']]
    result=[]
    for row in data['data']:
        d=dict(zip(cols,row));result.append(dict(id=str(d['source_id']),ra=d['ra'],dec=d['dec'],
            pmra=d['pmra'],pmdec=d['pmdec'],parallax=d['parallax'],g=d['phot_g_mean_mag']))
    return result
