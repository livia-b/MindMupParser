from __future__ import print_function
import json
import sys
import itertools
from jsonmodels import models, fields, errors, validators


class Attachment(models.Base):
    contentType = fields.StringField()
    content = fields.StringField()

class Style(models.Base):
    color = fields.StringField()
    lineStyle = fields.StringField()
    background = fields.StringField()

class Attributes(models.Base):
    style = fields.EmbeddedField(Style)
    collapsed = fields.BoolField(required=False)
    attachment = fields.EmbeddedField(Attachment, required=False)
    measurements = fields.EmbeddedField(models.Base)

class Link(models.Base):
    ideaIdFrom = fields.IntField(required=True)
    ideaIdTo = fields.IntField(required=True)
    attr = fields.EmbeddedField(Attributes)
    defaults = {
        'attr': {
            'style': {
                "color" : "#FF0000",
                "lineStyle" : "dashed"
            }
        }
    }

    def __init__(self, **kwargs):
        updatedKwargs = dict(self.defaults)
        updatedKwargs.update(kwargs)
        super(Link,self).__init__(**updatedKwargs)

    def setColor(self, color):
        self.populate(**{'attr':{'style':{'color': color}}})

    def setLineStyle(self, style):
        #style in dashed ,
        self.populate(**{'attr':{'style':{'lineStyle': style}}})


def MeasurementFactory(**fields):
    class Measurements(models.Base):
        pass
    for fieldName, fieldValue in fields.iteritems():
        setattr(Measurements, fieldName, fieldValue)
    return Measurements

class rootAttributes(models.Base):
    _measurements_config = fields.ListField([str])


class BaseIdea(models.Base):
    title = fields.StringField(required=True)
    id = fields.IntField(required=False) #anyway it will be processed by ValidateIdList
    attr = fields.EmbeddedField(Attributes, required=False)
    _ideas = fields.ListField(['BaseIdea']) #lazy loading for circular references, see doc

    def __str__(self):
        return "[%s] %s " % (self.id if hasattr(self,'id') else '?', self.title)

    @classmethod
    def walkSubTree(cls, root, level=0):
        depth = level + 1
        subIdeas = root._ideas
        yield root, subIdeas, depth
        for nextIdea in subIdeas:
            for x in cls.walkSubTree(nextIdea, level=depth):
                yield x

    def parse_to_mindmup(self, reassignId = False):
        if reassignId:
            self.id = next(reassignId)
        if len(self._ideas) == 0:
            self.setCollapse(False)
        mm = self.basefields_to_struct()
        if len(self._ideas):
            mm['ideas'] = {}
            for rank, idea in enumerate(self._ideas, start=1):
                mm['ideas'][str(rank)] = idea.parse_to_mindmup(reassignId)
        return mm

    def basefields_to_struct(self):
        mm = self.to_struct()
        mm.pop('_ideas', [])
        return mm

    def addAttachment(self, text, append=True):
        if not getattr(self, 'attr'):
            self.attr = Attributes()
        if self.attr.attachment:
            if append:
                text = self.attr.attachment.to_struct()['content'] + " <hr> " + text
        else:
            self.attr.attachment = Attachment()
        self.attr.attachment.populate(contentType= "text/html", content= text)

    def addMeasure(self, name, value):
        measurementFields = {}
        values = {}
        if not getattr(self, 'attr'):
            self.attr = Attributes()
        if self.attr.measurements:
            values = self.attr.measurements.to_struct()
            for fieldName, fieldValue in self.attr.measurements.iterate_over_fields():
                measurementFields[fieldName] = fieldValue

        measurementFields[name]= fields.StringField()
        values[name] = str(value)
        self.attr.measurements = MeasurementFactory(**measurementFields)(**values)

    def setCollapse(self, state):
        if not getattr(self, 'attr'):
            self.attr = Attributes()
        self.attr.collapsed = state

    def setColor(self, color):
        if not getattr(self, 'attr'):
            self.attr = Attributes()
        if not getattr(self.attr, 'style'):
            self.attr.style = Style()
        self.attr.style.background = color


class MindMupRootNode(BaseIdea):
    attr = fields.EmbeddedField(rootAttributes)
    links = fields.ListField([Link])
    formatVersion = fields.IntField(validators=[validators.Max(2), validators.Min(2)])
    defaults = {'formatVersion': 2}


class MindMupManager(MindMupRootNode):
    def __init__(self,  *args, **kwargs):
        self.idList = {}
        self._linksManager = dict()
        updatedKwargs = dict(MindMupRootNode.defaults)
        updatedKwargs.update(kwargs)
        super(MindMupManager,self).__init__(**updatedKwargs)
        if len(args):
            if isinstance(args[0], str):
                self.parseMindMupFile(args[0])

    def manageLink(self, idea1, idea2, action = "add", **styleKwargs):
        if action=="add":
            style = dict(Link.defaults)['attr']['style']
            style.update(styleKwargs)
            self._linksManager[idea1, idea2] = style
        elif action == "remove":
            self._linksManager.pop((idea1, idea2), None)

    def updateMeasurements(self):
        measurements = set()
        for idea, subIdeas, depth in self.walkSubTree(self):
            meas = getattr(idea.attr, 'measurements',None)
            if not meas:
                meas = []
            for name, field in meas:
                measurements.add(name)

        if not getattr(self, 'attr'):
            self.attr = rootAttributes()
        for m in measurements:
            self.attr._measurements_config.append(m)

    def updateIdList(self, raiseOnDuplicate = True):
        self.idList.clear()
        for idea, subIdeas, depth in self.walkSubTree(self):
            curId = idea.id
            while curId in self.idList:
                if raiseOnDuplicate:
                    raise Exception(ValueError, "DuplicateId %s" %curId)
                curId = '_'+ str(curId)
            self.idList[curId] = idea


    def reorderIds(self):
        self.idList.clear()
        for root, subIdeas, depth in self.walkSubTree(self):
            root.id = len(self.idList) + 1
            self.idList[root.id] = root

    def updateLinkList(self):
        self.updateIdList(raiseOnDuplicate=True)
        self.links = []
        for (idea1, idea2), style in self._linksManager.iteritems():
            styleField = Style(**style)
            self.links.append(Link(ideaIdFrom = idea1.id, ideaIdTo = idea2.id)) #, attr= {'style': styleField}))

    def to_mindmup(self, autoIncrement = True):
        self.updateMeasurements()
        if autoIncrement:
            reassignId = itertools.count(start=1)
        else:
            reassignId = False
        mm = self.parse_to_mindmup(reassignId=reassignId)
        self.updateIdList(raiseOnDuplicate=True)
        if self._linksManager:
            self.updateLinkList()
            mm =self.parse_to_mindmup(reassignId=False)
        if mm['attr'].has_key('_measurements_config'):
            mm['attr']['measurements-config'] =  mm['attr'].pop('_measurements_config')
        return mm

    @classmethod
    def _parseNodes(cls, node):
        #opposite of to_mindmup
        ideas = node.pop('ideas', {})
        node['_ideas'] = []
        if node['id'] == 1:
            new = MindMupRootNode(**node)
        else:
            new = BaseIdea(**node)
        try:
            for m, value in node['attr']['measurements'].iteritems():
                new.addMeasure(m,value)
        except:
            pass
        for i in ideas.values():
            new._ideas.append(cls._parseNodes(i))
        return new

    def parseMindMupFile(self, mapFile):
        import simplejson
        with open(mapFile) as f:
            mm_dict = simplejson.load(f, encoding='utf-8')
        baseMap = self._parseNodes(mm_dict)
        fields = {k: getattr(baseMap,k) for k, v in baseMap.iterate_over_fields()}
        fields.pop('attr',None)
        self.populate(**fields)
        self.updateMeasurements(self)
        self.updateIdList(self)
        for link in self.links:
            self._linksManager[self.idList[link.ideaIdFrom], self.idList[link.ideaIdTo]] = link.attr.style.to_struct()




if __name__ == '__main__':

    map = MindMupManager()
    map.populate(title="test")
    subIdea = BaseIdea(title ="base1")
    subIdea.addMeasure('A',"1")
    subIdea.addMeasure('B',"1")
    subIdea.addMeasure('A',"2")
    map._ideas.append(subIdea)
    map._ideas.append(BaseIdea(title="idea2"))
    map.manageLink(*map._ideas[:2])

    from pprint import  pprint
    pprint(map._linksManager)
    print("converted map")
    pprint(map.to_mindmup())
    print('idlist', map.idList)

    with open('/tmp/test.mup', 'wb') as f:
        json.dump(map.to_mindmup(), f, indent=1)


    map2 = MindMupManager('/tmp/test.mm')
    print("read map again")
    pprint(map2.to_mindmup())


def dictToHtmlTable(aDict, caption="", tableProps = "border='1px solid black' 	border-collapse='collapse'",
                    maxElements = 3, maxDepth=5 , actionOnValue = None):
    """
    Print a dict as an html table that can be used as an attachment
    Args:
        aDict:
        caption:

    Returns:
        html text

    """
    html = "<table %s >" \
           "<caption><b>%s</b>" \
           "</caption>" %(tableProps, caption)
    maxDepth -= 1
    if maxDepth <0:
        return ""
    for key, value in aDict.iteritems():
        if actionOnValue:
            value = actionOnValue(value)
        if isinstance(value,dict):
            cell = dictToHtmlTable(value, tableProps="", maxElements= maxElements, maxDepth = maxDepth)
        elif isinstance(value, list):
            cell = "["
            for item in value[:maxElements]:
                if isinstance(item, dict):
                    cell += dictToHtmlTable(item, tableProps="", maxElements= maxElements, maxDepth = maxDepth)
                else:
                    cell += str(item)
                cell += ' '
            if len(value) > maxElements:
                cell += ' (... other %s elements)' %(len(value) - maxElements)
            cell += ']'
        else:
            cell = value
        html = "%s\n" \
               "<tr> <td><b>%s</b> </td>" \
               "<td>%s</td>" % (html, key, cell)
    html = "%s\n</table>" % html
    return html


class sharedNodeManager(object):
    """
    Class (dict-like) for managing a node and its children (indexed by user-defined keys). T
    he root node will be created if necessary.
    """
    def __init__(self, rootNode = None, rootKey = "root", defaultNodeConstructor = None, **kwargs):
        if defaultNodeConstructor is not None:
            self.defaultNodeConstructor = defaultNodeConstructor
        if rootNode is None:
            rootNode = BaseIdea(**self.defaultNodeConstructor(rootKey))
        rootNode.populate(**kwargs)
        self.rootNode = rootNode
        self.index = {}  # title, node
        if not rootKey is None:
            self.setNode(rootKey, rootNode)

    def getRootNode(self):
        return self.rootNode

    def defaultNodeConstructor(self, key):
        return {'title': str(key),
                'collapsed': True}

    def getNode(self, key):
        """
        Gets the node corresponding to key. If it doesn't exist, it is created and appended to the root node
        Args:
            key:

        Returns: node

        """
        node = self.index.get(key)
        if not node:
            node = BaseIdea(**self.defaultNodeConstructor(key))
            self.index[key] = node
            self.rootNode._ideas.append(node)
        return node

    def popNode(self, key, useDefault = False):
        """
        removes the node corresponding to key
        Args:
            key:
            useDefault:

        Returns:

        """
        node = self.index.pop(key, None)
        if node and node is not self.rootNode:
            self.getRootNode()._ideas.remove(node)
        elif useDefault:
            node = BaseIdea(**self.defaultNodeConstructor(key))
        return node

    def setNode(self, key, node):
        self.index[key] = node

    def initializeKeys(self, keysIterable):
        for k in keysIterable:
            self.index[k] = None