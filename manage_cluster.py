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
import string

#TODO: 
# - automatic detection of network to connect to
# - optional use of a local key instead of an OpenStack key
# - create floating ip if there isn't one?
# - handle creating an ssh network rule
# - allow addition of other network rules such as opening port 80 for http
# - If some of these things are automated then could just provide the command
#   to connect to the machine at the end of the setup.
# - maybe add a way to automatically tell when the system is all setup, check 
#   if cloud-init is still running? Pull down log of master?
# - add ability to boot from volumes
# - add ability to mount volumes (may need to do this via cloud-init/salt)
# - improve the way salt minions connect to salt master (using keys or something)
# - depending on the type of cluster being created will need to limit the
#   flavours, for example the hadoop cluster should be created with "c" flavour
#   nodes as the configuration scripts are configured to use the ephemeral disk
#   Persistent web-servers should boot off of volumes and thus probably won't
#   need the extra ephemeral storage. I also need to think about making this 
#   work for west cloud (i.e. include their flavors) and also for the new cc
#   clouds.

#maximum amount of time to wait for node to boot before skip rest of setup
maxWaitTimeForNodeBoot=20
keepTmpFiles=False#set to true if one wishes to keep temporary files for debugging
settingsFilePath=None

class NoBoot(Exception):
  pass
class Node(object):
  """Class for working with compute nodes in OpenStack using the Python API in 
  a nicer simpler way that hides much of the complexity.
  
  It however imposes some amount of extra structure not present in OpenStack
  such as unique node names and may not expose all the functionality present
  in the lower level OpenStack python API.
  """
  
  def __del__(self):
    """remove temporary files
    """
    
    if not keepTmpFiles:
      
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
    self._validateHostName()#check for a valid hostname
  def _validateHostName(self):
    """source:
    https://en.wikipedia.org/wiki/Hostname#Restrictions_on_valid_host_names
    
    1) must be under 253 characters
    2) each label (seperated by ".") must be between 1 and 63 characters long
    3) each label must contain only ASCII letters 'a' - 'Z' (case-insensitive)
      , '0' - '9', and '-'
    4) labels must not start or end with a '-'
    5) must be case-insensitive (i.e. will convert upper case to lower case)
    
    """
    
    allowed=set(string.ascii_lowercase+string.digits+"-"+string.ascii_uppercase)
    
    #get hostname
    hostname=self.xmlSettings.find("name").text
    
    #1) check for overall length
    if(len(hostname)>252):
      raise Exception("hostname \""+hostname+"\" is longer than 253 characters")
    
    labels=hostname.split(".")
    
    
    for label in labels:
      
      #2) check for length of label
      if not (len(label) <= 63 and len(label) >= 1):
        raise Exception("hostname label \""+label+"\" is "+str(len(label))
        +" characters long which is not between 1 and 63 characters long")
      
      #3) check for invalid characters
      if not (set(label) <= allowed):
        raise Exception("hostname label \""+label
          +"\" contains characters which are not allowed, \""
          +str(set(label)-allowed)+"\"")
      
      #4) must not start with a '-'
      if label[0]=='-':
        raise Exception("label \""+label
        +"\" starts with a '-' which is not allowed")
    
    
    self.xmlSettings.find("name").text=self.xmlSettings.find("name").text.lower()
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
  def _createUserDataFile(self,nodes):
    """Returns a file object pointing to the user data file
    """
    
    #if there isn't a cloud init file given nothing to do
    if( self.xmlSettings.find("cloud-init")==None ):
      return None
      
    
    #if there aren't any replace nodes
    hasReplaces=False
    if self.xmlSettings.find("cloud-init").find("replaces")!=None:#if there is a replaces element
      if self.xmlSettings.find("cloud-init").find("replaces").findall("replace")!=None:
        hasReplaces=True
    
    if not hasReplaces:
      return open(self.xmlSettings.find("cloud-init").find("file").text,'r')
    
    #need to create a temporary file with values replaced that doesn't already
    #exist
    fileName=os.path.join(settingsFilePath
      ,str(self.xmlSettings.find("cloud-init").find("file").text))
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
    
    #perform replaces on file as needed
    replaces=[]
    
    for xmlReplace in self.xmlSettings.find("cloud-init").find("replaces").findall("replace"):
      
      replaces.append((xmlReplace.find("match").text
        ,xmlReplace.find("nodes-with-property").text
        ,xmlReplace.find("property").text))
    
    for replace in replaces:
      
      #get list of nodes with the given property
      matchingNodes=[]
      for node in nodes:
        xmlProperties=node.xmlSettings.find("properties").findall("property")
        
        for xmlProperty in xmlProperties:
          if xmlProperty.text==replace[1]:
            matchingNodes.append(node)
      
      toReplace=""
      
      #handle fixed_ip
      if(replace[2]=="fixed_ip"):
        
        for matchingNode in matchingNodes:
        
          existingNode=self.nova.servers.find(
            name=matchingNode.xmlSettings.find("name").text)
          
          #if on more than one network, we get confused
          if len(existingNode.networks.keys())>1:
            raise Exception("node with name \""+replace[1]
              +"\" belongs to more than one network can not determine which "
              +"fixed_ip to use")
          
          #the replacement text
          #TODO: this may not be very general and should be tested more
          toReplace+=str(existingNode.networks[existingNode.networks.keys()[0]][0])+" "
        
      if(replace[2]=="name"):
        toReplace=""
        for matchingNode in matchingNodes:
          toReplace+=matchingNode.xmlSettings.find("name").text+" "
      
      #do the replacement
      inFileContent=inFileContent.replace(replace[0],str(toReplace))
    
    #open temporary settings file
    outFile=open(tmpFileName,'w')
    
    #write out the new file after all replacements are done
    outFile.write(inFileContent)
    outFile.close()
    
    #return the temporary file with replacements done
    self.outFile=open(tmpFileName,'r')
    self.tmpFileName=tmpFileName
    return self.outFile
  def _createNewNode(self,nodes):
    """Boots a new node
    """
    
    print("    Creating new node ",end="")
    
    #Get parameters for creating a node
    image=self.nova.images.find(name=self.xmlSettings.find("image").text)
    flavor=self.nova.flavors.find(name=self.xmlSettings.find("flavor").text)
    net=self.nova.networks.find(label=self.xmlSettings.find("network").text)
    nics=[{'net-id':net.id}]
    
    userDataFile=self._createUserDataFile(nodes)
    
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
      raise NoBoot("      WARNING: node took too long to boot, setup may be "
        +"incomplete.")
      
    
    #at this point the node should be booted
    print()#add a new line
    print("      node booted")
    self.instance=server#assign updated version of server
      
    #if we have a floating ip add it
    if self.xmlSettings.find("floating-ip")!=None:
      self._assignFloatingIP()
  def create(self,nodes):
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
      try:
        self._createNewNode(nodes)
      except NoBoot as e:
        print()
        print(e.args[0])
        print("      deleting node and trying again ... ")
        self.delete()
        self._createNewNode(nodes)
      
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
    
    #Initialize node list
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
      node.create(self.nodes)
      
    print("it may take some time for any cloud-init operations to complete, try "
      +"logging into a node and running ps -A | grep \"cloud-init\" to see if "
      +"cloud-init is still running")
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
  
  global settingsFilePath
  settingsFilePath=os.path.dirname(args[0])
  #print("settingsFilePath=",settingsFilePath)
  
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
  
  #check to see if the environment has the expected variables
  envVars=env.keys()
  requiredVars=["OS_AUTH_URL","OS_USERNAME","OS_PASSWORD","OS_TENANT_NAME"
    ,"OS_REGION_NAME"]
  for var in requiredVars:
    
    if( var not in envVars):
      raise Exception("environment variable \""+var
        +"\" not found, did you source the cloud *-openrc.sh file?")
  
  #create the nova client
  nova=nvclient.Client(auth_url=env['OS_AUTH_URL']
    ,username=env['OS_USERNAME'],api_key=env['OS_PASSWORD']
    ,project_id=env['OS_TENANT_NAME'],region_name=env['OS_REGION_NAME'])
  
  #Initialize the cluster
  cluster=Cluster(xmlCluster,nova)
  
  #perform the given action on the cluster
  clusterAction=getattr(cluster,args[1])
  clusterAction()
if __name__ == "__main__":
  main()