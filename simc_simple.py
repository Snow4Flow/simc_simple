# IGNORE THIS FILE FOR NOW...NEEDS MORE WORK

import argparse
import configparser
import rasterio as rio
import numpy as np
import os
import pandas as pd
import tqdm
from pyproj import CRS, Transformer
import sys

def parseCmd():

    # Build argparser and parse command line args
    parser = argparse.ArgumentParser(description="Run a clutter simulation.")
    parser.add_argument("confPath", 
                        help="Path to configuration file (.ini)")
    parser.add_argument("-n",
                        dest="navPath",
                        help="Path to navigation file - overrides any path in config file")
    parser.add_argument("-d",
                        dest="demPath",
                        help="Path to DEM file - overrides any path in config file")
    parser.add_argument("-o",
                        dest="outPath",
                        help="Path to output products - overrides any path in config file")
    parser.add_argument("-p", 
                        action="store_true", 
                        help="Display progress bar")
    args = parser.parse_args()

    # Store in dict, expand any relative paths
    argDict             = {}
    argDict["confPath"] = os.path.abspath(args.confPath)
    argDict["p"]        = args.p

    if args.navPath is not None:
        argDict["navPath"] = os.path.abspath(args.navPath)
    else:
        argDict["navPath"] = None
    if args.demPath is not None:
        argDict["demPath"] = os.path.abspath(args.demPath)
    else:
        argDict["demPath"] = None
    if args.outPath is not None:
        argDict["outPath"] = os.path.abspath(args.outPath)
    else:
        argDict["outPath"] = None

    return argDict

def readConfig(argDict):
    """
    Reads in config file and command line args into dict. Checks legality of various parameters in it.

    Returns:
        Dict: Config parameters.
    """
    # Check that config file path is valid
    if not os.path.exists(argDict["confPath"]):
        print("Invalid path to config file - file does not exist.")
        sys.exit(1)

    config = configparser.ConfigParser()

    try:
        config.read(argDict["confPath"])
    except Exception as err:
        print("Unable to parse config file.")
        print(err)
        sys.exit(1)

    confDict = {section: dict(config.items(section)) for section in config.sections()}  # dict of config file

    # Substitute in command line args if necessary,  command line arg overrides config file
    if argDict["navPath"] is not None:       
        confDict["paths"]["navpath"] = argDict["navPath"]
    if argDict["demPath"] is not None:
        confDict["paths"]["dempath"] = argDict["demPath"]
    if argDict["outPath"] is not None:
        confDict["paths"]["outpath"] = argDict["outPath"]
    if confDict["paths"]["sigpath"].strip() not in (None, ""):
        confDict["simParams"]["coherent"] = True
    else:
        confDict["simParams"]["coherent"] = False

    # Check that nav, out, and dem paths are valid
    if not os.path.exists(confDict["paths"]["navpath"]):
        print("Invalid path to navigation file - file does not exist.")
        sys.exit(1)
    if not os.path.exists(confDict["paths"]["dempath"]):
        print("Invalid path to DEM file - file does not exist.")
        sys.exit(1)
    if not os.path.exists(confDict["paths"]["outpath"]):
        print("Invalid path to output files - folder does not exist.")
        sys.exit(1)
    if not os.path.exists(confDict["paths"]["sigpath"]):
        print("Invalid path to signal file - file does not exist.")
        sys.exit(1)
        # Load signal to use for coherent simulation
        confDict["simParams"]["signal"] = np.loadtxt(confDict["paths"]["sigpath"], 
                                                     dtype=np.complex128)

    # Make output prefix
    if confDict["paths"]["outpath"][-1] != "/":
        confDict["paths"]["outpath"] += "/"

    navfile                         = confDict["paths"]["navpath"].split("/")[-1]
    navname                         = navfile.split(".")[0]
    confDict["paths"]["outpath"]    = confDict["paths"]["outpath"] + navname + "_"
    
    # Assign correct data types for non-string config items
    confDict["simParams"]["speedlight"]     = float(confDict["simParams"]["speedlight"])
    confDict["simParams"]["dt"]             = float(confDict["simParams"]["dt"])
    confDict["simParams"]["tracesamples"]   = int(confDict["simParams"]["tracesamples"])

    confDict["facetParams"]["atdist"] = float(confDict["facetParams"]["atdist"])
    confDict["facetParams"]["ctdist"] = float(confDict["facetParams"]["ctdist"])
    confDict["facetParams"]["atstep"] = float(confDict["facetParams"]["atstep"])
    confDict["facetParams"]["ctstep"] = float(confDict["facetParams"]["ctstep"])

    boolDict = {"true": True, "t": True, "false": False, "f": False}

    for key in confDict["outputs"].keys():
        if confDict["outputs"][key].lower() in boolDict:
            confDict["outputs"][key] = boolDict[confDict["outputs"][key].lower()]
        else:
            print("Invalid value for outputs:" + key)
            print('Must be "True" or "False"')
            sys.exit(1)

    # Check that facet extent and dimensions are legal
    if confDict["facetParams"]["atdist"] < confDict["facetParams"]["atstep"]:
        print("Invalid config file param.")
        print("atdist must be greater than atstep")
        sys.exit(1)

    if confDict["facetParams"]["ctdist"] < confDict["facetParams"]["ctstep"]:
        print("Invalid config file param.")
        print("ctdist must be greater than ctstep")
        sys.exit(1)

    if confDict["facetParams"]["atdist"] % confDict["facetParams"]["atstep"]:
        print("Invalid config file param")
        print("atdist must be integer multiple of atstep")
        sys.exit(1)

    if confDict["facetParams"]["ctdist"] % confDict["facetParams"]["ctstep"]:
        print("Invalid config file param")
        print("ctdist must be integer multiple of ctstep")
        sys.exit(1)

    confDict["navigation"]["xyzsys"] = CRS.from_epsg(4978).to_wkt()
    confDict["navigation"]["llesys"] = CRS.from_epsg(4326).to_wkt()

    return confDict

def simc_simple(cfg, nav):
    
    argDict     = parseCmd()
    confDict    = readConfig(argDict)
    dem         = rio.open(confDict["paths"]["dempath"], 
                           mode="r")
    
    required = ["x", "y", "z", "datum"]
    for r in required:
        if r not in nav.keys():
            raise RuntimeError(
                "Missing necessary field in navigation file.\n\tRequired fields: %s\n\tFound fields: %s"
                % (required, list(nav.keys())))

    # transform navigation x/y/z from its CRS to xyz CRS
    nav["x"], nav["y"], nav["z"] = Transformer.from_crs(crs_from=confDict["navigation"]["navsys"], 
                                                        crs_to=confDict["navigation"]["xyzsys"]).transform(nav["x"].to_numpy(), 
                                                                                                           nav["y"].to_numpy(),
                                                                                                           nav["z"].to_numpy())

    # Parse DEM CRS
    xform           = Transformer.from_crs(confDict["navigation"]["xyzsys"], 
                                           CRS.from_user_input(dem.crs))
    nav, oDict, inv = simc_prep(confDict, nav)
    bounds          = calcBounds(dem,
                                 dem.crs,
                                 nav,
                                 confDict["navigation"]["xyzsys"],
                                 confDict["facetParams"]["atdist"],
                                 confDict["facetParams"]["ctdist"])

    rowSub = (bounds[2], bounds[3] + 1)
    colSub = (bounds[0], bounds[1] + 1)

    win     = rio.windows.Window.from_slices(rowSub, colSub)
    demData = dem.read(1, 
                       window=win)
    gt      = ~demData.window_transform(win) # # geotransform DEM x,y to pixel coordinates

    for i in tqdm.tqdm(range(nav.shape[0]), 
                       disable=(not argDict["p"])):
        fcalc = simc_sim(confDict, dem, nav, xform, demData, gt, i)
        if fcalc.shape[0] == 0:
            continue
        
        # Putting things back in order
        simc_build(confDict, oDict, fcalc, nav["datum"][i], np.where(inv == i)[0])

    simc_save(confDict, oDict, nav.iloc[inv, :].reset_index(), dem, win, demData)
    dem.close()

    return cgram, fret