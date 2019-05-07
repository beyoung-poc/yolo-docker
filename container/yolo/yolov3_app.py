# pylint: disable=R, W0401, W0614, W0703
import timeit
import time
from ctypes import *
import flask
import logging
import requests
import os
from io import StringIO

#import signal
#import traceback


#<editor-fold desc="Configure Environment - Start Flask,  pull Funcs from C library, etc.">

# Begin timer for environment configuration
start = timeit.default_timer()

bucket = None

with open("/opt/program/configs", "r") as f:
    for line in f:
        split = line.split("=")
        if str(split[0]) == "bucket":
            bucket = str(split[1].strip())


# Create TimeStamp/Job ID  (not suitable for more than 1-2 calls per second)
def getJobID():
    return str(time.time()).replace(".", "-")

JOB_ID = getJobID()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(JOB_ID)


previous_log_token = None

lib = CDLL("./libyolo_volta.so", RTLD_GLOBAL)

# Get length of C values
def c_array(ctype, values):
    arr = (ctype*len(values))()
    arr[:] = values
    return arr


# Declare some constant Enums
class BOX(Structure):
    _fields_ = [("x", c_float),
                ("y", c_float),
                ("w", c_float),
                ("h", c_float)]


class DETECTION(Structure):
    _fields_ = [("bbox", BOX),
                ("classes", c_int),
                ("prob", POINTER(c_float)),
                ("mask", POINTER(c_float)),
                ("objectness", c_float),
                ("sort_class", c_int)]


class IMAGE(Structure):
    _fields_ = [("w", c_int),
                ("h", c_int),
                ("c", c_int),
                ("data", POINTER(c_float))]


class METADATA(Structure):
    _fields_ = [("classes", c_int),
                ("names", POINTER(c_char_p))]


# Declare some network constants at start up
lib.network_width.argtypes = [c_void_p]
lib.network_width.restype = c_int
lib.network_height.argtypes = [c_void_p]
lib.network_height.restype = c_int

copy_image_from_bytes = lib.copy_image_from_bytes
copy_image_from_bytes.argtypes = [IMAGE, c_char_p]


def network_width(net):
    return lib.network_width(net)


def network_height(net):
    return lib.network_height(net)


# Pull all the C funcs into Python-land
predict = lib.network_predict_ptr
predict.argtypes = [c_void_p, POINTER(c_float)]
predict.restype = POINTER(c_float)

set_gpu = lib.cuda_set_device
set_gpu.argtypes = [c_int]

make_image = lib.make_image
make_image.argtypes = [c_int, c_int, c_int]
make_image.restype = IMAGE

get_network_boxes = lib.get_network_boxes
get_network_boxes.argtypes = [c_void_p, c_int, c_int, c_float, c_float, POINTER(c_int), c_int, POINTER(c_int), c_int]
get_network_boxes.restype = POINTER(DETECTION)

make_network_boxes = lib.make_network_boxes
make_network_boxes.argtypes = [c_void_p]
make_network_boxes.restype = POINTER(DETECTION)

free_detections = lib.free_detections
free_detections.argtypes = [POINTER(DETECTION), c_int]

free_ptrs = lib.free_ptrs
free_ptrs.argtypes = [POINTER(c_void_p), c_int]

network_predict = lib.network_predict_ptr
network_predict.argtypes = [c_void_p, POINTER(c_float)]

reset_rnn = lib.reset_rnn
reset_rnn.argtypes = [c_void_p]

load_net = lib.load_network
load_net.argtypes = [c_char_p, c_char_p, c_int]
load_net.restype = c_void_p

load_net_custom = lib.load_network_custom
load_net_custom.argtypes = [c_char_p, c_char_p, c_int, c_int]
load_net_custom.restype = c_void_p

do_nms_obj = lib.do_nms_obj
do_nms_obj.argtypes = [POINTER(DETECTION), c_int, c_int, c_float]

do_nms_sort = lib.do_nms_sort
do_nms_sort.argtypes = [POINTER(DETECTION), c_int, c_int, c_float]

free_image = lib.free_image
free_image.argtypes = [IMAGE]

letterbox_image = lib.letterbox_image
letterbox_image.argtypes = [IMAGE, c_int, c_int]
letterbox_image.restype = IMAGE

load_meta = lib.get_metadata
lib.get_metadata.argtypes = [c_char_p]
lib.get_metadata.restype = METADATA

load_image = lib.load_image_color
load_image.argtypes = [c_char_p, c_int, c_int]
load_image.restype = IMAGE

rgbgr_image = lib.rgbgr_image
rgbgr_image.argtypes = [IMAGE]

predict_image = lib.network_predict_image
predict_image.argtypes = [c_void_p, IMAGE]
predict_image.restype = POINTER(c_float)


thresh = 0.25

# HardCode these variables for now
config_path = "/opt/program/aces.cfg"
weight_path = "/opt/program/aces_4000.weights"
meta_path = "/opt/program/aces.data"
net_main = load_net_custom(config_path.encode("ascii"), weight_path.encode("ascii"), 0, 1)  # batch size = 1
meta_main = load_meta(meta_path.encode("ascii"))
image_path = "/opt/program/test.jpg"

# Load the class names
with open("/opt/program/aces.names") as namesFH:
    names_list = namesFH.read().strip().split("\n")

stop = timeit.default_timer()
param_load_time = stop-start

# Start the Flask server
app = flask.Flask(__name__)
# </editor-fold>


def array_to_image(arr):
    import numpy as np
    # need to return old values to avoid python freeing memory
    arr = arr.transpose(2, 0, 1)
    c = arr.shape[0]
    h = arr.shape[1]
    w = arr.shape[2]
    arr = np.ascontiguousarray(arr.flat, dtype=np.float32) / 255.0
    data = arr.ctypes.data_as(POINTER(c_float))
    im = IMAGE(w, h, c, data)
    return im, arr


def classify(net, meta, im):
    out = predict_image(net, im)
    res = []
    for i in range(meta.classes):
        name_tag = meta.names[i]
        res.append((name_tag, out[i]))
    res = sorted(res, key=lambda x: -x[1])
    return res


def detect(net, meta, image, thresh=.5, hier_thresh=.5, nms=.45):
    ret = None
    try:
        # pylint: disable= C0321
        im = load_image(image, 0, 0)

        ret = detect_image(net, meta, im, thresh, hier_thresh, nms)
        free_image(im)
    except Exception as err:
        print("detect(): {}".format(err))

    return ret


def detect_image(net, meta, im, thresh=.5, hier_thresh=.5, nms=.45):

    start_detection = timeit.default_timer()
    inferences = None

    try:
        num = c_int(0)

        pnum = pointer(num)

        predict_image(net, im)

        # dets = get_network_boxes(net, custom_image_bgr.shape[1], custom_image_bgr.shape[0], thresh, hier_thresh, None, 0, pnum, 0) # OpenCV
        dets = get_network_boxes(net, im.w, im.h, thresh, hier_thresh, None, 0, pnum, 0)

        num = pnum[0]

        if nms:
            do_nms_sort(dets, num, meta.classes, nms)

        res = []

        for j in range(num):
            for i in range(meta.classes):
                if dets[j].prob[i] > 0:
                    b = dets[j].bbox
                    name_tag = meta.names[i]
                    res.append((name_tag, dets[j].prob[i], (b.x, b.y, b.w, b.h)))

        stop_detection = timeit.default_timer()
        detection_time = stop_detection - start_detection

        # TODO - this is hacky - should be a JSON.dumps or pretty print type capabilities to clean this up
        inferences = "{"
        inferences = inferences + "'jobID':" + "'" + JOB_ID + "',"
        inferences = inferences + "'time-to-infer':" + str(detection_time) + ","
        inferences = inferences + "'inferences':"
        for i in res:
            inferences = inferences + "{"
            index = 0
            suit_rank = None
            confidence = None
            box_coords = None
            for section in i:  # should be three - SUIT + RANK, CONFIDENCE, Coords
                index += 1
                if index is 1:  # SUIT+RANK
                    suit_rank = section
                    inferences = inferences + "'SuiteRank':'" + suit_rank.decode("utf-8") + "',"
                elif index is 2:
                    confidence = section
                    inferences = inferences + "'Confidence':'" + str(confidence) + "',"
                else:
                    box_coords = section
                    inferences = inferences + "'Coords':'" + str(box_coords) + "'}"
                    index = 0
        inferences = inferences + "}"
    except Exception as err:
        print("detect_image(): {}".format(err))

    return inferences


@app.route('/ping', methods=['GET'])
def ping():
    """
    Verification function to ensure the application works.  Runs inference on a built-in test image
    :return:
    """
    status = 200
    try:
        # Return the detection results from the test image to verify functionality
        result = detect(net_main, meta_main, image_path.encode("ascii"), thresh)
    except Exception as err:
        result = err

    return flask.Response(response=result, status=status, mimetype='application/json')


@app.route('/invocations', methods=['POST'])
def invocations():

    debug = True
    result = "no result"
    status = 200
    try:
        if debug:
            print("invocations method called")

        if flask.request.content_type == "image/jpeg":  # Image bytes have been sent
            if debug:
                print("looks like the content-type is 'image/jpeg'")

            fname = flask.request.files['file']
            fname.save('/opt/program/images/' + fname.filename)
            image_path = '/opt/program/images/' + fname.filename
            result = detect(net_main, meta_main, image_path.encode("ascii"), thresh)

        else:  # Path to image on S3 has been sent

            if flask.request.content_type == "application/json":
                if debug:
                    print("looks like the content-type is 'application/json'")

                j = flask.request.get_json()
                try:
                    s3Path = j['key']

                except:
                    print("json keyword 'key' was not found!")
                    raise

                url = "https://s3.amazonaws.com/" + bucket + s3Path
                if debug: print("looking for {}".format(url))

                # Download file to local
                if os.path.isfile('/opt/program/images/' + s3Path):
                    os.remove('/opt/program/images/' + s3Path)

                    observation = requests.get(url)
                    open('/opt/program/images/' + s3Path, 'wb').write(observation.content)

                    image_path = '/opt/program/images/' + s3Path
                    result = detect(net_main, meta_main, image_path.encode("ascii"), thresh)
    except Exception as err:
        status = 500
        result = err


    return flask.Response(response=result, status=status, mimetype='application/json')


# Accept an S3 URL path to the image to inference against - object must be public
@app.route('/s3/<s3Path>')
def s3(s3Path):
    """
    :param s3Path: the URL of the image to be referenced
    :return: the inference results of the image at the provided URL
    """
    status = 200
    try:

        url = "https://s3.amazonaws.com/" + bucket + s3Path
        # Download file to local
        if os.path.isfile('/tmp/'+ s3Path):
            os.remove('/tmp/' + s3Path)


        observation = requests.get(url)
        open('/tmp/' + s3Path, 'wb').write(observation.content)

        image_path = '/tmp/' + s3Path
        result = detect(net_main, meta_main, image_path.encode("ascii"), thresh)
    except Exception as err:
        result = err
        status = 500

    return flask.Response(response=result, status=status, mimetype='application/json')