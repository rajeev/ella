from datetime import datetime, date

from django.contrib.contenttypes.models import ContentType
from django.template import RequestContext
from django.core.paginator import Paginator
from django.conf import settings
from django.template.defaultfilters import slugify
from django.db import models
from django.http import Http404

from ella.core.models import Listing, Category, Placement
from ella.core.cache import get_cached_object_or_404, cache_this
from ella.core import custom_urls
from ella.core.cache.template_loader import render_to_response

__docformat__ = "restructuredtext en"

# local cache for get_content_type()
CONTENT_TYPE_MAPPING = {}
CACHE_TIMEOUT_LONG = getattr(settings, 'CACHE_TIMEOUT_LONG', 60 * 60)

class EllaCoreView(object):
    ' Base class for class-based views used in ella.core.views. '

    # name of the template to be passed into get_templates
    template_name = 'TEMPLATE'

    def get_context(self, request, **kwargs):
        """
        Return a dictionary that will be then passed into the template as context.

        :Parameters:
            - `request`: current request

        :Returns:
            Dictionary with all the data
        """
        raise NotImplementedError()

    def get_templates(self, context):
        " Extract parameters for `get_templates` from the context. "
        kw = {}
        if 'object' in context:
            o = context['object']
            kw['slug'] = o.slug

        if context.get('content_type', False):
            ct = context['content_type']
            kw['app_label'] = ct.app_label
            kw['model_label'] = ct.model

        return get_templates(self.template_name, category=context['category'], **kw)

    def render(self, request, context, template):
        return render_to_response(template, context,
            context_instance=RequestContext(request))

    def __call__(self, request, **kwargs):
        context = self.get_context(request, **kwargs)
        return self.render(request, context, self.get_templates(context))

class CategoryDetail(EllaCoreView):
    """
    Homepage of a category. Renders a templates using context containing:
        - `category`: the root `Category` of the site
        - `is_homepage`: boolean whether the category is the root category
        - `archive_entry_year`: year of last `Listing`

    :Parameters: 
        - `request`: `HttpRequest` from Django
        - `category`: optional - `tree_path` of the `Category` to render
          home page is used if this parameter is omitted

    :Exceptions: 
        - `Http404`: if there is no matching category
    """
    template_name = 'category.html'

    def _archive_entry_year(self, category):
        " Return ARCHIVE_ENTRY_YEAR from settings (if exists) or year of the newest object in category "
        year = getattr(settings, 'ARCHIVE_ENTRY_YEAR', None)
        if not year:
            now = datetime.now()
            try:
                year = Listing.objects.filter(
                        category__site__id=settings.SITE_ID,
                        category__tree_path__startswith=category.tree_path,
                        publish_from__lte=now
                    ).values('publish_from')[0]['publish_from'].year
            except:
                year = now.year
        return year


    def get_context(self, request, category=''):
        cat = get_cached_object_or_404(Category, tree_path=category, site__id=settings.SITE_ID)
        context = {
                    'category' : cat,
                    'is_homepage': not bool(category),
                    'archive_entry_year' : self._archive_entry_year(cat),
                }

        return context

class ObjectDetail(EllaCoreView):
    """
    Renders a page for placement. All the data fetching and context creation is done in `_object_detail`.
    If `url_remainder` is specified, tries to locate custom view via `custom_urls.dispatcher`. Renders a template 
    returned by `get_template` with context containing:

        - `placement`: `Placement` instance representing the URL accessed
        - `object`: `Publishable` instance bound to the `placement`
        - `category`: `Category` of the `placement`
        - `content_type_name`: slugified plural verbose name of the publishable's content type
        - `content_type`: `ContentType` of the publishable

    :Parameters:
        - `request`: `HttpRequest` from Django
        - `category`: `Category.tree_path` (empty if home category)
        - `content_type`: slugified `verbose_name_plural` of the target model
        - `year`, `month`, `day`: date matching the `publish_from` field of the `Placement` object
        - `slug`: slug of the `Placement`
        - `url_remainder`: url after the object's url, used to locate custom views in `custom_urls.dispatcher`

    :Exceptions: 
        - `Http404`: if the URL is not valid and/or doesn't correspond to any valid `Placement`
    """
    template_name = 'object.html'
    def __call__(self, request, category, content_type, slug, year=None, month=None, day=None, url_remainder=None):
        context = self.get_context(request, category, content_type, slug, year, month, day)

        obj = context['object']
        # check for custom actions
        if url_remainder:
            bits = url_remainder.split('/')
            return custom_urls.dispatcher.call_view(request, bits, context)
        elif custom_urls.dispatcher.has_custom_detail(obj):
            return custom_urls.dispatcher.call_custom_detail(request, context)

        return self.render(request, context, self.get_templates(context))

    def get_context(self, request, category, content_type, slug, year, month, day):
        ct = get_content_type(content_type)

        cat = get_cached_object_or_404(Category, tree_path=category, site__id=settings.SITE_ID)

        if year:
            placement = get_cached_object_or_404(Placement,
                        publish_from__year=year,
                        publish_from__month=month,
                        publish_from__day=day,
                        publishable__content_type=ct,
                        category=cat,
                        slug=slug,
                        static=False
                    )
        else:
            placement = get_cached_object_or_404(Placement, category=cat, publishable__content_type=ct, slug=slug, static=True)

        # save existing object to preserve memory and SQL
        placement.category = cat
        placement.publishable.content_type = ct


        if not (placement.is_active() or request.user.is_staff):
            # future placement, render if accessed by logged in staff member
            raise Http404

        obj = placement.publishable.target

        context = {
                'placement' : placement,
                'object' : obj,
                'category' : cat,
                'content_type_name' : content_type,
                'content_type' : ct,
            }

        return context

class ListContentType(EllaCoreView):
    """
    List objects' listings according to the parameters.

    :Parameters:
        - `category`: base Category tree_path, optional - defaults to all categories
        - `year, month, day`: date matching the `publish_from` field of the `Placement` object.
          All of these parameters are optional, the list will be filtered by the non-empty ones
        - `content_type`: slugified verbose_name_plural of the target model, if omitted all content_types are listed
        - `page_no`: which page to display
        - `paginate_by`: number of records in one page

    :Exceptions:
        - `Http404`: if the specified category or content_type does not exist or if the given date is malformed.
    """
    template_name = 'listing.html'
    def get_context(self, request, category='', year=None, month=None, day=None, content_type=None, paginate_by=20):
        # pagination
        if 'p' in request.GET and request.GET['p'].isdigit():
            page_no = int(request.GET['p'])
        else:
            page_no = 1

        kwa = {}
        year = int(year)
        kwa['publish_from__year'] = year

        if month:
            try:
                month = int(month)
                date(year, month, 1)
            except ValueError, e:
                raise Http404()
            kwa['publish_from__month'] = month

        if day:
            try:
                day = int(day)
                date(year, month, day)
            except ValueError, e:
                raise Http404()
            kwa['publish_from__day'] = day

        cat = get_cached_object_or_404(Category, tree_path=category, site__id=settings.SITE_ID)
        kwa['category'] = cat
        if category:
            kwa['children'] = Listing.objects.ALL

        if content_type:
            ct = get_content_type(content_type)
            kwa['content_types'] = [ ct ]
        else:
            ct = False

        qset = Listing.objects.get_queryset_wrapper(kwa)
        paginator = Paginator(qset, paginate_by)

        if page_no > paginator.num_pages or page_no < 1:
            raise Http404()

        page = paginator.page(page_no)
        listings = page.object_list

        context = {
                'page': page,
                'is_paginated': paginator.num_pages > 1,
                'results_per_page': paginate_by,

                'content_type' : ct,
                'content_type_name' : content_type,
                'listings' : listings,
                'category' : cat,
            }

        return context

# backwards compatibility
object_detail = ObjectDetail()
home = category_detail = CategoryDetail()
list_content_type = ListContentType()

def get_content_type(ct_name):
    """
    A helper function that returns ContentType object based on its slugified verbose_name_plural.

    Results of this function is cached to improve performance.

    :Parameters: 
        - `ct_name`:  Slugified verbose_name_plural of the target model.

    :Exceptions: 
        - `Http404`: if no matching ContentType is found
    """
    try:
        ct = CONTENT_TYPE_MAPPING[ct_name]
    except KeyError:
        for model in models.get_models():
            if ct_name == slugify(model._meta.verbose_name_plural):
                ct = ContentType.objects.get_for_model(model)
                CONTENT_TYPE_MAPPING[ct_name] = ct
                break
        else:
            raise Http404
    return ct



def get_templates(name, slug=None, category=None, app_label=None, model_label=None):
    """
    Returns templates in following format and order:

        - 'page/category/%s/content_type/%s.%s/%s/%s' % ( category.path, app_label, model_label, slug, name ),
        - 'page/category/%s/content_type/%s.%s/%s' % ( category.path, app_label, model_label, name ),
        - 'page/category/%s/%s' % ( category.path, name ),
        - 'page/content_type/%s.%s/%s' % ( app_label, model_label, name ),
        - 'page/%s' % name,
    """
    templates = []
    if category:
        if app_label and model_label:
            if slug:
                templates.append('page/category/%s/content_type/%s.%s/%s/%s' % (category.path, app_label, model_label, slug, name))
            templates.append('page/category/%s/content_type/%s.%s/%s' % (category.path, app_label, model_label, name))
        templates.append('page/category/%s/%s' % (category.path, name))
    if app_label and model_label:
        templates.append('page/content_type/%s.%s/%s' % (app_label, model_label, name))
    templates.append('page/%s' % name)
    return templates


def get_templates_from_placement(name, placement, slug=None, category=None, app_label=None, model_label=None):
    """
    Returns the same template list as `get_templates` but generates the missing values from `Placement` instance.
    """
    if slug is None:
        slug = placement.slug
    if category is None:
        category = placement.category
    if app_label is None:
        app_label = placement.publishable.content_type.app_label
    if model_label is None:
        model_label = placement.publishable.content_type.model
    return get_templates(name, slug, category, app_label, model_label)


def get_export_key(func, request, count, name='', content_type=None):
    return 'ella.core.views.export:%d:%d:%s:%s' % (
            settings.SITE_ID, count, name, content_type
        )

@cache_this(get_export_key, timeout=CACHE_TIMEOUT_LONG)
def export(request, count, name='', content_type=None):
    """
    Export banners.

    :Parameters:
        - `count`: number of objects to pass into the template
        - `name`: name of the template ( page/export/banner.html is default )
        - `models`: list of Model classes to include
    """
    t_list = []
    if name:
        t_list.append('page/export/%s.html' % name)
    t_list.append('page/export/banner.html')

    cat = get_cached_object_or_404(Category, tree_path='', site__id=settings.SITE_ID)
    listing = Listing.objects.get_listing(count=count, category=cat)
    return render_to_response(
            t_list,
            { 'category' : cat, 'listing' : listing },
            context_instance=RequestContext(request),
            content_type=content_type
        )


##
# Error handlers
##
def page_not_found(request):
    response = render_to_response('page/404.html', {}, context_instance=RequestContext(request))
    response.status_code = 404
    return response

def handle_error(request):
    response = render_to_response('page/500.html', {}, context_instance=RequestContext(request))
    response.status_code = 500
    return response
