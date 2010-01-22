
from twisted.web.error import NoResource
from twisted.web import html, static
from twisted.web.util import Redirect

import re, urllib, time
from twisted.python import log
from buildbot import interfaces
from buildbot.status.web.base import HtmlResource, BuildLineMixin, \
    path_to_build, path_to_slave, path_to_builder, path_to_change, \
    getAndCheckProperties
from buildbot.process.base import BuildRequest
from buildbot.sourcestamp import SourceStamp

from buildbot.status.web.build import BuildsResource, StatusResourceBuild
from buildbot import util

# /builders/$builder
class StatusResourceBuilder(HtmlResource, BuildLineMixin):
    addSlash = True

    def __init__(self, builder_status, builder_control):
        HtmlResource.__init__(self)
        self.builder_status = builder_status
        self.builder_control = builder_control

    def getTitle(self, request):
        return "Buildbot: %s" % self.builder_status.getName()

    def builder(self, build, req):
        b = {}

        b['num'] = build.getNumber()
        b['link'] = path_to_build(req, build)

        when = b['when'] = build.getETA()
        if when is not None:
            b['when_time'] = time.strftime("%H:%M:%S",
                                      time.localtime(time.time() + when))
                    
        step = build.getCurrentStep()
        if step:
            b['current_step'] = step.getName()
        else:
            b['current_step'] = "[waiting for Lock]"
            # TODO: is this necessarily the case?

        builder_control = self.getControl(req)
        if builder_control is not None:
            b['stop_url'] = path_to_build(req, build) + '/stop'

        return b
          
    def content(self, req, cxt):
        b = self.builder_status
        control = self.builder_control
        status = self.getStatus(req)

        cxt['name'] = b.getName()
        
        slaves = b.getSlaves()
        connected_slaves = [s for s in slaves if s.isConnected()]

        cxt['current'] = [self.builder(x, req) for x in b.getCurrentBuilds()]        
            
        cxt['pending'] = []        
        for pb in b.getPendingBuilds():
            changes = []

            if pb.source.changes:
                for c in pb.source.changes:
                    changes.append({ 'url' : path_to_change(req, c),
                                     'who' : c.who})
                reason = "see changes"
            elif pb.source.revision:
                reason = pb.source.revision
            else:
                reason = "no changes specified"
                    
            cxt['pending'].append({
                'when': time.strftime("%b %d %H:%M:%S", time.localtime(pb.getSubmitTime())),
                'delay': util.formatInterval(util.now() - pb.getSubmitTime()),
                'reason': reason,
                'id': id(pb),
                'changes' : changes
                })

        if self.builder_control is not None:
            cxt['cancel_url'] = path_to_builder(req, b) + '/cancelbuild'
                        
        numbuilds = int(req.args.get('numbuilds', ['5'])[0])
        recent = cxt['recent'] = []
        for build in b.generateFinishedBuilds(num_builds=int(numbuilds)):
            recent.append(self.get_line_values(req, build, False))

        sl = cxt['slaves'] = []
        for slave in slaves:
            s = {}
            sl.append(s)
            s['link'] = path_to_slave(req, slave)
            s['name'] = slave.getName()
            c = s['connected'] = slave.isConnected()
            if c:
                s['admin'] = slave.getAdmin()

        if control is not None and connected_slaves:
            cxt['force_url'] = path_to_builder(req, b) + '/force'
            cxt['use_user_passwd'] = self.isUsingUserPasswd(req)
        elif control is not None:
            cxt['all_slaves_offline'] = True

        if control is not None:
            cxt['ping_url'] = path_to_builder(req, b) + '/ping'

        template = req.site.buildbot_service.templates.get_template("builder.html")
        return template.render(**cxt)

    def force(self, req):
        """

        Custom properties can be passed from the web form.  To do
        this, subclass this class, overriding the force() method.  You
        can then determine the properties (usually from form values,
        by inspecting req.args), then pass them to this superclass
        force method.
        
        """
        name = req.args.get("username", ["<unknown>"])[0]
        reason = req.args.get("comments", ["<no reason specified>"])[0]
        branch = req.args.get("branch", [""])[0]
        revision = req.args.get("revision", [""])[0]

        r = "The web-page 'force build' button was pressed by '%s': %s\n" \
            % (html.escape(name), html.escape(reason))
        log.msg("web forcebuild of builder '%s', branch='%s', revision='%s'"
                " by user '%s'" % (self.builder_status.getName(), branch,
                                   revision, name))

        if not self.builder_control:
            # TODO: tell the web user that their request was denied
            log.msg("but builder control is disabled")
            return Redirect("..")

        if self.isUsingUserPasswd(req):
            if not self.authUser(req):
                return Redirect("../../authfail")

        # keep weird stuff out of the branch revision, and property strings.
        # TODO: centralize this somewhere.
        if not re.match(r'^[\w\.\-\/]*$', branch):
            log.msg("bad branch '%s'" % branch)
            return Redirect("..")
        if not re.match(r'^[\w\.\-\/]*$', revision):
            log.msg("bad revision '%s'" % revision)
            return Redirect("..")
        properties = getAndCheckProperties(req)
        if properties is None:
            return Redirect("..")
        if not branch:
            branch = None
        if not revision:
            revision = None

        # TODO: if we can authenticate that a particular User pushed the
        # button, use their name instead of None, so they'll be informed of
        # the results.
        # TODO2: we can authenticate that a particular User pushed the button
        # now, so someone can write this support. but it requires a
        # buildbot.changes.changes.Change instance which is tedious at this
        # stage to compute
        s = SourceStamp(branch=branch, revision=revision)
        req = BuildRequest(r, s, builderName=self.builder_status.getName(),
                           properties=properties)
        try:
            self.builder_control.requestBuildSoon(req)
        except interfaces.NoSlaveError:
            # TODO: tell the web user that their request could not be
            # honored
            pass
        # send the user back to the builder page
        return Redirect(".")

    def ping(self, req):
        log.msg("web ping of builder '%s'" % self.builder_status.getName())
        self.builder_control.ping() # TODO: there ought to be an ISlaveControl
        # send the user back to the builder page
        return Redirect(".")

    def cancel(self, req):
        try:
            request_id = req.args.get("id", [None])[0]
            if request_id == "all":
                cancel_all = True
            else:
                cancel_all = False
                request_id = int(request_id)
        except:
            request_id = None
        if request_id:
            for build_req in self.builder_control.getPendingBuilds():
                if cancel_all or id(build_req.original_request.status) == request_id:
                    log.msg("Cancelling %s" % build_req)
                    build_req.cancel()
                    if not cancel_all:
                        break
        return Redirect(".")

    def getChild(self, path, req):
        if path == "force":
            return self.force(req)
        if path == "ping":
            return self.ping(req)
        if path == "events":
            num = req.postpath.pop(0)
            req.prepath.append(num)
            num = int(num)
            # TODO: is this dead code? .statusbag doesn't exist,right?
            log.msg("getChild['path']: %s" % req.uri)
            return NoResource("events are unavailable until code gets fixed")
            filename = req.postpath.pop(0)
            req.prepath.append(filename)
            e = self.builder_status.getEventNumbered(num)
            if not e:
                return NoResource("No such event '%d'" % num)
            file = e.files.get(filename, None)
            if file == None:
                return NoResource("No such file '%s'" % filename)
            if type(file) == type(""):
                if file[:6] in ("<HTML>", "<html>"):
                    return static.Data(file, "text/html")
                return static.Data(file, "text/plain")
            return file
        if path == "cancelbuild":
            return self.cancel(req)
        if path == "builds":
            return BuildsResource(self.builder_status, self.builder_control)

        return HtmlResource.getChild(self, path, req)


# /builders/_all
class StatusResourceAllBuilders(HtmlResource, BuildLineMixin):

    def __init__(self, status, control):
        HtmlResource.__init__(self)
        self.status = status
        self.control = control

    def getChild(self, path, req):
        if path == "force":
            return self.force(req)
        if path == "stop":
            return self.stop(req)

        return HtmlResource.getChild(self, path, req)

    def force(self, req):
        for bname in self.status.getBuilderNames():
            builder_status = self.status.getBuilder(bname)
            builder_control = None
            c = self.getControl(req)
            if c:
                builder_control = c.getBuilder(bname)
            build = StatusResourceBuilder(builder_status, builder_control)
            build.force(req)
        # back to the welcome page
        return Redirect("../..")

    def stop(self, req):
        for bname in self.status.getBuilderNames():
            builder_status = self.status.getBuilder(bname)
            builder_control = None
            c = self.getControl(req)
            if c:
                builder_control = c.getBuilder(bname)
            (state, current_builds) = builder_status.getState()
            if state != "building":
                continue
            for b in current_builds:
                build_status = builder_status.getBuild(b.number)
                if not build_status:
                    continue
                if builder_control:
                    build_control = builder_control.getBuild(b.number)
                else:
                    build_control = None
                build = StatusResourceBuild(build_status, build_control,
                                            builder_control)
                build.stop(req)
        # go back to the welcome page
        return Redirect("../..")


# /builders
class BuildersResource(HtmlResource):
    title = "Builders"
    addSlash = True

    def content(self, req, ctx):
        s = self.getStatus(req)

        ctx['builders'] = builders = []
        for bname in s.getBuilderNames():
            builders.append({'link' : req.childLink(urllib.quote(bname, safe='')),
                             'name' : bname})
                      
        template = req.site.buildbot_service.templates.get_template('builders.html')
        return template.render(ctx)

    def getChild(self, path, req):
        s = self.getStatus(req)
        if path in s.getBuilderNames():
            builder_status = s.getBuilder(path)
            builder_control = None
            c = self.getControl(req)
            if c:
                builder_control = c.getBuilder(path)
            return StatusResourceBuilder(builder_status, builder_control)
        if path == "_all":
            return StatusResourceAllBuilders(self.getStatus(req),
                                             self.getControl(req))

        return HtmlResource.getChild(self, path, req)

