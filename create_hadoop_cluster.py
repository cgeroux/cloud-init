#!/usr/bin/env python
import optparse as op
import subprocess
import time

class VM(object):
  def __init__(self):

def addParserOptions(parser):
  """Adds command line options
  """
  
  saltMasterOptions=op.OptionGroup(parser,"Salt Master Options")
  
  #these options apply globally
  saltMasterOptions.add_option("--salt-master-image"
    ,dest="saltMasterImage"
    ,type="string"
    ,default="Ubuntu_14.04_Trusty-amd64-20150708"
    ,help="Sets the image to use for the salt_master node [default: %default]")
  saltMasterOptions.add_option("--salt-master-cloud-init"
    ,dest="saltMasterCloudInit"
    ,type="string"
    ,default="master-cloudconfig"
    ,help="Cloud-init file to use to create salt_master [default: %default]")
  saltMasterOptions.add_option("--salt-master-flavor"
    ,dest="saltMasterFlavor"
    ,type="string"
    ,default="p1-0.75gb"
    ,help="Flavor to use for salt_master [default: %default]")
  saltMasterOptions.add_option("--salt-master-key-name"
    ,dest="saltMasterKeyName"
    ,type="string"
    ,default="Vivian"
    ,help="Name of the key pair to inject into salt_master [default: %default]")
  saltMasterOptions.add_option("--salt-master-floating-ip"
    ,dest="saltMasterFloatingIP"
    ,type="string"
    ,default="206.167.181.71"
    ,help="Floating IP address to associate with salt_master" \
    +" [default: %default]")
def parseOptions():
  """Parses command line options
  
  """
  
  parser=op.OptionParser(usage="Usage: %prog [options] "
    ,version="%prog 1.0",description="")
  
  #add options
  addParserOptions(parser)
  
  #parse command line options
  return parser.parse_args()
def getLocalIpOfVM(VMName):
  time.sleep(5)
  cmd=["nova","list"]
  process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  stdout,stderr=process.communicate()
  print stdout
  print stderr
  
  #pull out lines with actual info
  linesStdout=stdout.splitlines()
  infoLines=linesStdout[3:-1]
  for infoLine in infoLines:
    fields=infoLine.split("|")
    ips=fields[6].split('=')[1].split(',')
    print len(ips),"ips:",
  
def createVM():
  
def main():
  
  #parse command line options
  (options,args)=parseOptions()
  
  
  
  print "Settings for salt_master:"
  print "  saltMasterImage=",options.saltMasterImage
  print "  saltMasterCloudInit=",options.saltMasterCloudInit
  print "  saltMasterFlavor=",options.saltMasterFlavor
  print "  saltMasterKeyName=",options.saltMasterKeyName
  print "  saltMasterFloatingIP=",options.saltMasterFloatingIP
  
  #nova boot --image options.saltMasterImage 
  #--user-data options.saltMasterCloudInit --flavor options.saltMasterFlavor 
  #--key-name options.saltMasterKeyName salt_master
  cmd=["nova","boot"
    ,"--image",options.saltMasterImage
    ,"--user-data",options.saltMasterCloudInit
    ,"--flavor",options.saltMasterFlavor
    ,"--key-name",options.saltMasterKeyName
    ,"--key-name",options.saltMasterKeyName
    ,"salt_master"
    ]
  process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  stdout,stderr=process.communicate()
  print stdout
  print stderr
  
  #now wait some time and then assign a floating-ip
  time.sleep(5)
  cmd=["nova","add-floating-ip","salt_master",options.saltMasterFloatingIP]
  process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  stdout,stderr=process.communicate()
  print stdout
  print stderr
  
  #create hadoop-master (get local salt_master ip)
  
  
if __name__ == "__main__":
  main()