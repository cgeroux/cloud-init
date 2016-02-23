#!/usr/bin/env python
from __future__ import print_function
import optparse as op
#import xml.etree.ElementTree as xml
from lxml import etree
from lxml import objectify
import time
from os import environ as env
import novaclient.v1_1.client as nvclient
import novaclient
import sys
import os
import copy

#maximum amount of time to wait for node to boot before skip rest of setup
maxWaitTimeForNodeBoot=20

class Node(object):
  """Class for working with compute nodes in OpenStack using the Python API in 
  a nicer simpler way that hides much of the complexity.
  
  It however imposes some amount of extra structure not present in OpenStack
  such as unique node names and may not expose all the functionality present
  in the lower level OpenStack python API.
  """
  
  def __del__(self):
    """
    """
    
    #if we used a temporary file close and remove it
    if self.tmpFileName!=None:
      self.outFile.close()
      os.remove(self.tmpFileName)
      self.tmpFileName=None
      self.outFile=None
  def __init__(self,xmlNode,nova):
    """Parse the XML element nodeElement to initialize the settings for the node
    """
    
    self.nova=nova
    self.xmlSettings=xmlNode
    self.tmpFileName=None
  def _assignFloatingIP(self):
    """Assigns a floating ip if not already assigned to a node and it is 
    available for use
    """
    
    #check that the ip exists
    ipList=self.nova.floating_ips.list()
    requestedIPExists=False
    ipToUse=None
    xmlIPSetting=self.xmlSettings.find("floating-ip").text
    for ip in ipList:
      
      #if we found the ip we wanted to assign in the list
      if ip.ip==xmlIPSetting:
        requestedIPExists=True
        ipToUse=ip
    
    #if ip doesn't exist, don't add it
    if not requestedIPExists:
      print("    WARNING: The requested floating ip "
        +xmlIPSetting
        +"\" does not exist. Not assigning it to node.")
      return
    
    #If ip is already assigned to a node don't reassign it
    if(ipToUse.instance_id!=None):
      server=self.nova.servers.find(id=ipToUse.instance_id)
      print("    WARNING: floating ip is already assigned to the \""
        +server.name+"\" node; not reassigning it.")
      return
    
    #assign the ip to the node
    print("    Adding floating ip "+xmlIPSetting+" ...")
    self.instance.add_floating_ip(ipToUse)
  def _createUserDataFile(self):
    """Returns a file object pointing to the user data file
    """
    
    #if there isn't a cloud init file given nothing to do
    if( self.xmlSettings.find("cloud-init")==None ):
      return None
      
    
    #if there aren't any replace nodes
    if self.xmlSettings.find("cloud-init").findall("replace")==None:
      return open(self.xmlSettings.find("cloud-init").find("file").text,'r')
    
    #need to create a temporary file with values replaced that doesn't already
    #exist
    fileName=str(self.xmlSettings.find("cloud-init").find("file").text)
    tmpFileName=fileName+".tmp"
    fileExists=os.path.isfile(tmpFileName)
    count=0
    while(fileExists):
      tmpFileName=fileName+str(count)+".tmp"
      count+=1
      #print(tmpFileName)
      fileExists=os.path.isfile(tmpFileName)
    
    #open reference file
    inFile=open(fileName,'r')
    inFileContent=inFile.read()
    
    #open temporary settings file
    outFile=open(tmpFileName,'w')
    self.tmpFileName=tmpFileName
    
    #perform replaces on file as needed
    replaces=[]
    for xmlReplace in self.xmlSettings.find("cloud-init").findall("replace"):
      
      replaces.append((xmlReplace.find("match").text
        ,xmlReplace.find("node-name").text
        ,xmlReplace.find("property").text))
    
    for replace in replaces:
      
      #get property of node
      existingNode=self.nova.servers.find(name=replace[1])
      
      toReplace=replace[0]#initialize so it does nothing
      if(replace[2]=="fixed_ip"):
      
        #if on more than one network, we get confused
        if len(existingNode.networks.keys())>1:
          raise Exception("node with name \""+replace[1]
            +"\" belongs to more than one network can not determine which "
            +"fixed_ip to use")
        
        #the replacement text
        #TODO: this may not be very general and should be tested more
        toReplace=str(existingNode.networks[existingNode.networks.keys()[0]][0])
      
      #do the replacement
      inFileContent=inFileContent.replace(replace[0],toReplace)
    
    #write out the new file after all replacements are done
    outFile.write(inFileContent)
    outFile.close()
    
    #return the temporary file with replacements done
    self.outFile=open(tmpFileName,'r')
    return self.outFile
  def _createNewNode(self):
    """Boots a new node
    """
    
    print("    Creating new node ",end="")
    
    #Get parameters for creating a node
    image=self.nova.images.find(name=self.xmlSettings.find("image").text)
    flavor=self.nova.flavors.find(name=self.xmlSettings.find("flavor").text)
    net=self.nova.networks.find(label=self.xmlSettings.find("network").text)
    nics=[{'net-id':net.id}]
    
    userDataFile=self._createUserDataFile()
    
    if userDataFile==None:
      self.instance=self.nova.servers.create(
        name=self.xmlSettings.find("name").text
        ,image=image
        ,flavor=flavor
        ,key_name=self.xmlSettings.find("key-name").text
        ,nics=nics)
    else:
      self.instance=self.nova.servers.create(
        name=self.xmlSettings.find("name").text
        ,image=image
        ,flavor=flavor
        ,key_name=self.xmlSettings.find("key-name").text
        ,nics=nics
        ,userdata=userDataFile)
    
    #wait for it to spin up before trying to assign an ip
    iters=0
    server=self.nova.servers.find(name=self.xmlSettings.find("name").text)
    while (server.status!="ACTIVE" and iters<maxWaitTimeForNodeBoot):
      print(".",end="")
      sys.stdout.flush()
      time.sleep(1)
      server=self.nova.servers.find(name=self.xmlSettings.find("name").text)
      iters+=1
    
    #if node still not booted, skip any more setup
    if iters>=maxWaitTimeForNodeBoot:
      print("      WARNING: node took too long to boot, setup may be "
        +"incomplete.")
      return
    
    #at this point the node should be booted
    print()#add a new line
    print("      node booted")
    self.instance=server#assign updated version of server
      
    #if we have a floating ip add it
    if self.xmlSettings.find("floating-ip")!=None:
      self._assignFloatingIP()
  def create(self):
    """Creates a node if needed, and ensures node is active
    """
    
    nodeName=self.xmlSettings.find("name").text
    print("  booting the node \""+nodeName+"\" ...")
    try:
      
      #try getting an existing node, if it fails there may be no node
      #or multiple nodes
      existingNode=self.nova.servers.find(name=nodeName)
      
      #Check if the node is active
      #TODO: should handle more statuses correctly
      if(existingNode.status=="ACTIVE"):
        print("    Node is already active, nothing to be done")
      else:
        print("    WARNING: Node in an unknown state \""+existingNode.status
          +"\"; script doing nothing for this node; manual intervention may "
          +"be required")
    
    #no node with that name
    except novaclient.exceptions.NotFound:
      
      #no existing node found, create a new one
      self._createNewNode()
      
    #more than one match for that name
    except novaclient.exceptions.NoUniqueMatch:
      raise Exception("Multiple nodes found with a matching "
      +"name unable to determine which node is to be booted!")
  def delete(self):
    """deletes the node
    """
    nodeName=self.xmlSettings.find("name").text
    print("  deleting the node \""+nodeName+"\" ...")
    
    try:
      server=self.nova.servers.find(name=nodeName)
      self.nova.servers.delete(server)
      print("    Node deleted")
    except novaclient.exceptions.NotFound:
      print("    No node found with given name. Doing nothing")
class Cluster(object):
  """Container for managing a number of VM nodes
  """
  
  def __init__(self,clusterElement,nova):
    
    #Intialize node list
    self.nodes=[]
    for xmlNode in clusterElement:
      
      #get number of instances
      xmlNumInstances=xmlNode.find("num-instances")
      numInstances=1
      if xmlNumInstances!=None:
        numInstances=int(xmlNumInstances.text)
      
      #create the nodes
      if numInstances==1:#if only one node
        
        #create one instance
        self.nodes.append(Node(xmlNode,nova))
      else:
        
        #create numInstances with a suffix added to the name
        originalName=xmlNode.find("name").text
        for i in range(numInstances):
          
          #create a node
          xmlCurNode=copy.deepcopy(xmlNode)
          node=Node(xmlCurNode,nova)
          
          #adjust the node name
          xmlName=xmlCurNode.find("name")
          xmlName.text=originalName+"-"+str(i)
          self.nodes.append(node)
  def create(self):
    """Creates all the nodes in the cluster as needed and ensures they are all
    active
    """
    
    for node in self.nodes:
      node.create()
  def delete(self):
    """Deletes all the nodes in the cluster
    """
    
    for node in self.nodes:
      node.delete()
def addParserOptions(parser):
  """Adds command line options
  """
  
  pass
def parseOptions(actions):
  """Parses command line options
  
  """
  
  parser=op.OptionParser(usage="Usage: %prog [options] CONFIG.xml ACTION"
    ,version="%prog 1.0",description="Performs ACTION on the OpenStack "
    +"cluster described by CONFIG.xml. ACTION can be one of "+str(actions))
  
  #add options
  addParserOptions(parser)
  
  #parse command line options
  return parser.parse_args()
def main():
  
  #these actions should match methods in the Cluster class
  actions=["create","delete"]
  
  #these verbs are just for messages and there should be one for each 
  #corresponding action
  verbs=["creating","deleting"]
  
  #parse command line options
  (options,args)=parseOptions(actions)
  
  #check we got the expected number of arguments
  if (len(args)!=2):
    raise Exception("Expected two arguments, the xml configuration file "
    +"describing the cluster followed by an action.")
  
  #check we got an action we recognize
  if args[1] not in actions:
    raise Exception(args[1]+" not in known actions "+str(actions))
  
  print(verbs[actions.index(args[1])]+" the cluster described by \""
    +args[0]+"\" ...")
  
  #parse xml file
  #xmlFile=open(args[0])
  #xml=xmlFile.read()
  tree=etree.parse(args[0])
  xmlCluster=tree.getroot()
  
  #load schema to validate against
  schema=etree.XMLSchema(file="./cluster_settings.xsd")
  
  #validate against schema
  schema.assertValid(tree)
  
  #create the nova client
  nova=nvclient.Client(auth_url=env['OS_AUTH_URL']
    ,username=env['OS_USERNAME'],api_key=env['OS_PASSWORD']
    ,project_id=env['OS_TENANT_NAME'],region_name=env['OS_REGION_NAME'])
  
  #Initialize the cluster
  cluster=Cluster(xmlCluster,nova)
  
  #perform the given action on the cluster
  clusterAction=getattr(cluster,args[1])
  clusterAction()
  
  """
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
  """
if __name__ == "__main__":
  main()