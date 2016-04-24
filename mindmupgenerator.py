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
        return "[%d] %s " % (self.id, self.title)

    def parse_to_mindmup(self, reassignId = False):
        if reassignId:
            self.id = next(reassignId)

        mm = self.basefields_to_struct()

        if len(self._ideas):
            mm['ideas'] = {}
            for rank, idea in enumerate(self._ideas, start=1):
                mm['ideas'][str(rank)] =  idea.parse_to_mindmup(reassignId)
        return mm

    def basefields_to_struct(self):
        mm = self.to_struct()
        mm.pop('_ideas', [])
        return mm


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
        self.get('attr', Attributes()).populate(collapsed = state)

class MindMupRootNode(BaseIdea):
    attr = fields.EmbeddedField(rootAttributes)
    links = fields.ListField([Link])
    formatVersion = fields.IntField(validators=[validators.Max(2), validators.Min(2)])
    defaults = {'formatVersion': 2}


class MindMupManager(MindMupRootNode):
    def __init__(self,  *args, **kwargs):
        self.idList = {}
        self._measurementsManager = set()
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

    def updateMeasurements(self, root):
        if root is self:
            self._measurementsManager.clear()
        else:
            meas = getattr(root.attr, 'measurements',None)
            if not meas:
                meas = []
            for name, field in meas:
                self._measurementsManager.add(name)

        for idea in root._ideas:
            self.updateMeasurements(idea)

        if root is self:
            root.attr = rootAttributes()
            for m in self._measurementsManager:
                root.attr._measurements_config.append(m)

    def updateIdList(self, root, raiseOnDuplicate = True):
        if root is self:
            self.idList.clear()
        curId = root.id
        while curId in self.idList:
            if raiseOnDuplicate:
                raise Exception(ValueError, "DuplicateId %s" %curId)
            curId = '_'+ str(curId)
        self.idList[curId] = root
        for idea in root._ideas:
            self.updateIdList(idea)

    def reorderIds(self, root, clear = False):
        if clear:
            self.idList.clear()
        root.id = len(self.idList) + 1
        self.idList[root.id] = root
        for idea in root._ideas:
            self.reorderIds(idea)

    def updateLinkList(self):
        self.updateIdList(self, raiseOnDuplicate=True)
        self.links = []
        for (idea1, idea2), style in self._linksManager.iteritems():
            styleField = Style(**style)
            self.links.append(Link(ideaIdFrom = idea1.id, ideaIdTo = idea2.id)) # attr= {'style': styleField}))

    def to_mindmup(self, autoIncrement = True):
        self.updateMeasurements(self)
        if autoIncrement:
            reassignId = itertools.count(start=1)
        else:
            reassignId = False
        mm = self.parse_to_mindmup(reassignId=reassignId)
        self.updateIdList(self, raiseOnDuplicate=True)
        if self._linksManager:
            self.updateLinkList()
            mm =self.parse_to_mindmup(reassignId=False)
            print mm['links']
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
        print self.links
        self.populate(**fields)
        print self.links
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
    print "converted map"
    pprint(map.to_mindmup())
    print 'idlist', map.idList

    with open('/tmp/test.mup', 'wb') as f:
        json.dump(map.to_mindmup(), f, indent=1)


    map2 = MindMupManager('/tmp/test.mm')
    print("read map again")
    pprint(map2.to_mindmup())


