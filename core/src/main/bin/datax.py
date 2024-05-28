#!/usr/bin/env python
# -*- coding:utf-8 -*-

import codecs
import json
import os
import platform
# re 正则表达式处理模块
import re
import signal
import socket
import subprocess
import sys
import time
from optparse import OptionGroup
from optparse import OptionParser
from string import Template
# 判断当前python的版本是否为2，用于控制2和3版本的输出
ispy2 = sys.version_info.major == 2

def isWindows():
    return platform.system() == 'Windows'


DATAX_HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATAX_VERSION = 'DATAX-OPENSOURCE-3.0'
if isWindows():
    codecs.register(lambda name: name == 'cp65001' and codecs.lookup('utf-8') or None)
    CLASS_PATH = ("%s/lib/*") % (DATAX_HOME)
else:
    CLASS_PATH = ("%s/lib/*:.") % (DATAX_HOME)
LOGBACK_FILE = ("%s/conf/logback.xml") % (DATAX_HOME)
DEFAULT_JVM = "-Xms1g -Xmx1g -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=%s/log" % (DATAX_HOME)
DEFAULT_PROPERTY_CONF = "-Dfile.encoding=UTF-8 -Dlogback.statusListenerClass=ch.qos.logback.core.status.NopStatusListener -Djava.security.egd=file:///dev/urandom -Ddatax.home=%s -Dlogback.configurationFile=%s" % (
    DATAX_HOME, LOGBACK_FILE)
ENGINE_COMMAND = "java -server ${jvm} %s -classpath %s  ${params} com.alibaba.datax.core.Engine -mode ${mode} -jobid ${jobid} -job ${job}" % (
    DEFAULT_PROPERTY_CONF, CLASS_PATH)
REMOTE_DEBUG_CONFIG = "-Xdebug -Xrunjdwp:transport=dt_socket,server=y,address=9999"

RET_STATE = {
    "KILL": 143,
    "FAIL": -1,
    "OK": 0,
    "RUN": 1,
    "RETRY": 2
}


def getLocalIp():
    try:
        return socket.gethostbyname(socket.getfqdn(socket.gethostname()))
    except:
        return "Unknown"


def suicide(signum, e):
    global child_process
    if ispy2:
        print >> sys.stderr, "[Error] DataX receive unexpected signal %d, starts to suicide." % (signum)
    else:
        print("[Error] DataX receive unexpected signal %d, starts to suicide." % (signum), sys.stderr)

    if child_process:
        # 发送退出信号给子进程
        child_process.send_signal(signal.SIGQUIT)
        # 等待转储数据完成
        time.sleep(1)
        # kill子进程
        child_process.kill()
    if ispy2:
        print >> sys.stderr, "DataX Process was killed ! you did ?"
    else:
        print("DataX Process was killed ! you did ?", sys.stderr)
    # 通过枚举定义不同的退出类型
    sys.exit(RET_STATE["KILL"])


def register_signal():
    # 判断是否window平台
    if not isWindows():
        # 声明child_process为全局变量
        global child_process
        """
            注册信息
            信号数为2 (signal.SIGINT）
            信号为3（signal.SIGQUIT）,
            信号数为15（signal.SIGTERM）
        """
        signal.signal(2, suicide)
        signal.signal(3, suicide)
        signal.signal(15, suicide)


def getOptionParser():
    # 添加使用说明参数，%prog会被替换为当前文件/程序的名称---这里是datax.py
    usage = "usage: %prog [options] job-url-or-path"
    # 创建parser对象，传递usage参数
    parser = OptionParser(usage=usage)
    # 对命令说明进行分组,并添加标题和描述
    prodEnvOptionGroup = OptionGroup(parser, "Product Env Options",
                                     "Normal user use these options to set jvm parameters, job runtime mode etc. "
                                     "Make sure these options can be used in Product Env.")
    """
     args为选项参数，-j为短选项，--jvm为长选项，
     metavar为选项参数的说明，在使用-h或--help的时候打印在选项参数的后面
     dest为后续使用的options的属性值，可以通过options.jvmParameters获取选项参数值
     action为遇到此选项参数的行为，默认为store，若为store_true，则表明该选项参数需要布尔值
     default为当命令行没有此选项参数时，默认给的该选项参数的值
     help为参数说明
    """
    prodEnvOptionGroup.add_option("-j", "--jvm", metavar="<jvm parameters>", dest="jvmParameters", action="store",
                                  default=DEFAULT_JVM, help="Set jvm parameters if necessary.")
    prodEnvOptionGroup.add_option("--jobid", metavar="<job unique id>", dest="jobid", action="store", default="-1",
                                  help="Set job unique id when running by Distribute/Local Mode.")
    prodEnvOptionGroup.add_option("-m", "--mode", metavar="<job runtime mode>",
                                  action="store", default="standalone",
                                  help="Set job runtime mode such as: standalone, local, distribute. "
                                       "Default mode is standalone.")
    prodEnvOptionGroup.add_option("-p", "--params", metavar="<parameter used in job config>",
                                  action="store", dest="params",
                                  help='Set job parameter, eg: the source tableName you want to set it by command, '
                                       'then you can use like this: -p"-DtableName=your-table-name", '
                                       'if you have mutiple parameters: -p"-DtableName=your-table-name -DcolumnName=your-column-name".'
                                       'Note: you should config in you job tableName with ${tableName}.')
    prodEnvOptionGroup.add_option("-r", "--reader", metavar="<parameter used in view job config[reader] template>",
                                  action="store", dest="reader", type="string",
                                  help='View job config[reader] template, eg: mysqlreader,streamreader')
    prodEnvOptionGroup.add_option("-w", "--writer", metavar="<parameter used in view job config[writer] template>",
                                  action="store", dest="writer", type="string",
                                  help='View job config[writer] template, eg: mysqlwriter,streamwriter')
    parser.add_option_group(prodEnvOptionGroup)

    devEnvOptionGroup = OptionGroup(parser, "Develop/Debug Options",
                                    "Developer use these options to trace more details of DataX.")
    devEnvOptionGroup.add_option("-d", "--debug", dest="remoteDebug", action="store_true",
                                 help="Set to remote debug mode.")
    devEnvOptionGroup.add_option("--loglevel", metavar="<log level>", dest="loglevel", action="store",
                                 default="info", help="Set log level such as: debug, info, all etc.")
    # 向parser中添加组
    parser.add_option_group(devEnvOptionGroup)
    return parser


def generateJobConfigTemplate(reader, writer):
    readerRef = "Please refer to the %s document:\n     https://github.com/alibaba/DataX/blob/master/%s/doc/%s.md \n" % (
        reader, reader, reader)
    writerRef = "Please refer to the %s document:\n     https://github.com/alibaba/DataX/blob/master/%s/doc/%s.md \n " % (
        writer, writer, writer)
    print(readerRef)
    print(writerRef)
    jobGuid = 'Please save the following configuration as a json file and  use\n     python {DATAX_HOME}/bin/datax.py {JSON_FILE_NAME}.json \nto run the job.\n'
    print(jobGuid)
    jobTemplate = {
        "job": {
            "setting": {
                "speed": {
                    "channel": ""
                }
            },
            "content": [
                {
                    "reader": {},
                    "writer": {}
                }
            ]
        }
    }
    readerTemplatePath = "%s/plugin/reader/%s/plugin_job_template.json" % (DATAX_HOME, reader)
    writerTemplatePath = "%s/plugin/writer/%s/plugin_job_template.json" % (DATAX_HOME, writer)
    try:
        readerPar = readPluginTemplate(readerTemplatePath)
    except:
        print("Read reader[%s] template error: can\'t find file %s" % (reader, readerTemplatePath))
    try:
        writerPar = readPluginTemplate(writerTemplatePath)
    except:
        print("Read writer[%s] template error: : can\'t find file %s" % (writer, writerTemplatePath))
    jobTemplate['job']['content'][0]['reader'] = readerPar
    jobTemplate['job']['content'][0]['writer'] = writerPar
    print(json.dumps(jobTemplate, indent=4, sort_keys=True))


def readPluginTemplate(plugin):
    with open(plugin, 'r') as f:
        return json.load(f)


def isUrl(path):
    if not path:
        return False

    assert (isinstance(path, str))
    # 调用re模块中的match方法，判断path是否为http开头的url
    m = re.match(r"^http[s]?://\S+\w*", path.lower())
    if m:
        return True
    else:
        return False


def buildStartCommand(options, args):
    commandMap = {}
    # 默认的虚拟机参数
    tempJVMCommand = DEFAULT_JVM
    # 获取传递的虚拟机参数，并和默认的虚拟机参数拼接
    if options.jvmParameters:
        tempJVMCommand = tempJVMCommand + " " + options.jvmParameters
    # 获取默认的debug参数，并和传递的debug参数拼接
    if options.remoteDebug:
        tempJVMCommand = tempJVMCommand + " " + REMOTE_DEBUG_CONFIG
        # 打印本机ip
        print('local ip: ', getLocalIp())
    # 获取传递的loglevel参数，并和默认的loglevel参数拼接，该选项参数提供的默认值为info
    # 其中 % 为字符串格式化操作符，%s表示格式化一个对象为字符，最后打印的结果为，例：-Dloglevel=info
    if options.loglevel:
        tempJVMCommand = tempJVMCommand + " " + ("-Dloglevel=%s" % (options.loglevel))
    # 获取传递的mode参数，为运行模式，该选项参数提供的默认值为standalone
    if options.mode:
        commandMap["mode"] = options.mode

    # jobResource 可能是 URL，也可能是本地文件路径（相对,绝对）
    # 获取的是命令行的第一个参数，若执行命令为python datax.py {YOUR_JOB.json}，则args[0]为data.py
    jobResource = args[0]
    # 判断jobResource是否为http开头的url
    if not isUrl(jobResource):
        # 不是以http开头的url，则获取jobResource的绝对路径
        jobResource = os.path.abspath(jobResource)
        # 如果是file://开头的url，则去掉file://
        if jobResource.lower().startswith("file://"):
            jobResource = jobResource[len("file://"):]
    # 获取jobResource的文件名，-20表示获取文件名的后20个字符
    jobParams = ("-Dlog.file.name=%s") % (jobResource[-20:].replace('/', '_').replace('.', '_'))
    # 获取传递的params参数，并和jobParams拼接
    if options.params:
        jobParams = jobParams + " " + options.params
    # 获取传递的jobid参数，若没有传递该参数，默认值为-1
    if options.jobid:
        commandMap["jobid"] = options.jobid
    # 将获取到的参数放入commandMap中，其中mode、jobid、jvm、params、job为key
    commandMap["jvm"] = tempJVMCommand
    commandMap["params"] = jobParams
    commandMap["job"] = jobResource
    # Template(ENGINE_COMMAND) - 定义支持替换的字符串类
    # substitute - 替换ENGINE_COMMAND字符串中的${mode}、${jobid}、${jvm}、${params}、${job}为commandMap中key对应的value
    return Template(ENGINE_COMMAND).substitute(**commandMap)


def printCopyright():
    print('''
DataX (%s), From Alibaba !
Copyright (C) 2010-2017, Alibaba Group. All Rights Reserved.

''' % DATAX_VERSION)
    #没有flush方法的调用会在程序执行结束之后调用，有了flush方法的调用会立即刷新缓冲区将信息打印到控制台
    sys.stdout.flush()


if __name__ == "__main__":
    # 向控制台输出datax信息
    printCopyright()
    # 创建并获取parser对象
    parser = getOptionParser()
    # 解析命令行参数，sys.argv[1:]为默认参数，表示获取命令行参数的第一个参数之后的所有参数
    options, args = parser.parse_args(sys.argv[1:])
    # 读取命令行参数reader和writer的值
    if options.reader is not None and options.writer is not None:
        # 如果都不为空，则生成job配置模板（按照userGuid.md中的说明，脚本执行方式为python datax.py {YOUR_JOB.json}，并不会执行到这里）
        generateJobConfigTemplate(options.reader, options.writer)
        sys.exit(RET_STATE['OK'])
    if len(args) != 1:
        parser.print_help()
        sys.exit(RET_STATE['FAIL'])
    # 构建启动命令
    startCommand = buildStartCommand(options, args)
    # print startCommand
    # subprocess模块使用Popen开启一个子进程，以shell的形式执行startCommand命令
    child_process = subprocess.Popen(startCommand, shell=True)
    # 注册信号
    register_signal()
    (stdout, stderr) = child_process.communicate()

    sys.exit(child_process.returncode)
