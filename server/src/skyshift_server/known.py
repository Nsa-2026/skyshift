"""Contract-shaped known-object lookup; no JPL results relabelled as SkyBoT."""
def cone(ra,dec,r,mjd,*,cache=None):
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astropy.time import Time
    from astroquery.imcce import Skybot
    table=Skybot.cone_search(SkyCoord(ra,dec,unit='deg'),r*u.arcmin,Time(mjd,format='mjd'),location='500')
    return [dict(name=str(row['Name']),epoch_id=None,ra=float(row['RA']),dec=float(row['DEC']),
                 v=float(row['V']) if not __import__('numpy').ma.is_masked(row['V']) else None,source='skybot') for row in table]
