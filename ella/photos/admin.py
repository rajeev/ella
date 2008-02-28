from django import newforms as forms
from django.contrib import admin
from django.conf import settings

from tagging.models import TaggingInlineOptions

from ella.photos.models import FormatedPhoto, Format, Photo
from ella.photos.views import format_photo_json


class FormatedPhotoForm(forms.BaseForm):
    def clean(self):
        """
        Validation function that checks the dimensions of the crop whether it fits into the original and the format.
        """
        data = self.cleaned_data
        photo = data['photo']
        if (
            (data['crop_left'] >  photo.width) or
            (data['crop_top'] > photo.height) or
            ((data['crop_left'] + data['crop_width']) > photo.width) or
            ((data['crop_top'] + data['crop_height']) > photo.height)
):
            raise forms.ValidationError, ugettext("The specified crop coordinates do not fit into the source photo.")
        return data


class FormatOptions(admin.ModelAdmin):
    list_display = ('name', 'max_width', 'max_height', 'stretch', 'resample_quality',)
    list_filter = ('site', 'stretch', 'nocrop',)
    search_fields = ('name',)


class CropAreaWidget(forms.TextInput):
    class Media:
        JS_JQUERY = 'js/jquery.js'
        JS_INTERFACE = 'js/interface.js'
        JS_CROP = 'js/crop.js'
        CSS_CROP = 'css/crop.css'
        js = (
            settings.ADMIN_MEDIA_PREFIX + JS_JQUERY,
            settings.ADMIN_MEDIA_PREFIX + JS_INTERFACE,
            settings.ADMIN_MEDIA_PREFIX + JS_CROP,
)
        css = {
            'screen': (settings.ADMIN_MEDIA_PREFIX + CSS_CROP,),
}

    def __init__(self, attrs={}):
        super(CropAreaWidget, self).__init__(attrs={'class': 'crop'})


class FormatedPhotoInlineOptions(admin.TabularInline):
    model = FormatedPhoto
    def formfield_for_dbfield(self, db_field, **kwargs):
        if db_field.name in ['crop_width', 'crop_height', 'crop_left', 'crop_top']:
            kwargs['widget'] = CropAreaWidget
        return super(self.__class__, self).formfield_for_dbfield(db_field, **kwargs)


class PhotoOptions(admin.ModelAdmin):
    inlines = (FormatedPhotoInlineOptions, TaggingInlineOptions,)
    list_display = ('title', 'width', 'height', 'thumb') ## 'authors')
    list_filter = ('created',)
    prepopulated_fields = {'slug': ('title',)}
    search_fields = ('title', 'image', 'description',)

    def __call__(self, request, url):
        if url and url.endswith('json'):
            return format_photo_json(request, *url.split('/')[-3:-1])
        return super(PhotoOptions, self).__call__(request, url)


class FormatedPhotoOptions(admin.ModelAdmin):
    base_form = FormatedPhotoForm
    list_display = ('filename', 'format', 'width', 'height')
    list_filter = ('format',)
    search_fields = ('filename',)
    raw_id_fields = ('photo',)


admin.site.register(Format, FormatOptions)
admin.site.register(Photo, PhotoOptions)
admin.site.register(FormatedPhoto, FormatedPhotoOptions)
