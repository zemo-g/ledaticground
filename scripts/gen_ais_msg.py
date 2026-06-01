#!/usr/bin/env python3
# Encode a known AIS Type 1 position report (168 bits, per the AIS bit-field spec) to
# validate src/ais_parse.rail. Writes one byte per bit -> /tmp/ais_msg.bits + truth JSON.
import numpy as np, json
def putb(B,v,n):           # append v as n big-endian bits
    for i in range(n-1,-1,-1): B.append((v>>i)&1)
mmsi=366123456; sog=72; cog=2701; status=0
lat_deg=0.0; lon_deg=-0.0   # Detroit River
lat=int(round(lat_deg*600000)) & ((1<<27)-1)
lon=int(round(lon_deg*600000)) & ((1<<28)-1)
B=[]
putb(B,1,6); putb(B,0,2); putb(B,mmsi,30); putb(B,status,4)
putb(B,0,8); putb(B,sog,10); putb(B,0,1)            # rot, sog, accuracy
putb(B,lon,28); putb(B,lat,27); putb(B,cog,12); putb(B,270,9)
while len(B)<168: B.append(0)
np.array(B,np.uint8).tofile('/tmp/ais_msg.bits')
json.dump({"mmsi":mmsi,"lat":lat_deg,"lon":lon_deg,"sog":sog/10.0,"cog":cog/10.0},
          open('/tmp/ais_truth.json','w'))
print(f'encoded Type1: MMSI {mmsi}  lat {lat_deg}  lon {lon_deg}  sog {sog/10} kn  cog {cog/10}')
